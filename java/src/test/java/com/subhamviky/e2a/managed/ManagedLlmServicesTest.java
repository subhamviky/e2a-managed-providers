package com.subhamviky.e2a.managed;

import com.anthropic.client.okhttp.AnthropicOkHttpClient;
import com.google.genai.Client;
import com.google.genai.types.HttpOptions;
import com.openai.client.okhttp.OpenAIOkHttpClient;
import com.subhamviky.e2a.core.RequestContext;
import com.subhamviky.e2a.llm.LlmResponse;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Each service through its provider's real Java SDK against a local wire-replay server. */
class ManagedLlmServicesTest {

    private HttpServer server;
    private String base;
    private final List<String> paths = new ArrayList<>();
    private final List<String> bodies = new ArrayList<>();
    private String reply;

    private static final List<Map<String, String>> MESSAGES = List.of(
            Map.of("role", "system", "content", "You are the settlement audit agent."),
            Map.of("role", "user", "content", "Explain settlement S-1."));

    @BeforeEach
    void start() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            paths.add(exchange.getRequestURI().getPath());
            bodies.add(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            byte[] out = reply.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, out.length);
            exchange.getResponseBody().write(out);
            exchange.close();
        });
        server.start();
        base = "http://127.0.0.1:" + server.getAddress().getPort();
    }

    @AfterEach
    void stop() {
        server.stop(0);
    }

    @Test
    void claudeSendsEffortNotTemperatureAndPerCallSettingsWin() {
        reply = """
                {"id":"msg_1","type":"message","role":"assistant","model":"claude-opus-5-5",
                 "content":[{"type":"text","text":"S-1 was flagged."}],"stop_reason":"end_turn",
                 "stop_sequence":null,"usage":{"input_tokens":12,"output_tokens":5}}""";
        var service = new ClaudeLlmService(AnthropicOkHttpClient.builder().apiKey("offline").baseUrl(base)
                .maxRetries(0).build(), "claude-opus-5-5");
        RequestContext ctx = RequestContext.begin("acme").withSettings(Map.of("reasoning_effort", "high"));
        LlmResponse response = service.infer(MESSAGES, ctx);
        assertEquals("S-1 was flagged.", response.text());
        assertEquals(17, response.tokensUsed());
        assertEquals("/v1/messages", paths.get(0));
        assertTrue(bodies.get(0).contains("\"effort\":\"high\""), bodies.get(0));
        assertTrue(!bodies.get(0).contains("temperature"));
    }

    @Test
    void claudeRefusalIsTyped() {
        reply = """
                {"id":"msg_2","type":"message","role":"assistant","model":"claude-opus-5-5","content":[],
                 "stop_reason":"refusal","stop_sequence":null,"usage":{"input_tokens":1,"output_tokens":0}}""";
        var service = new ClaudeLlmService(AnthropicOkHttpClient.builder().apiKey("offline").baseUrl(base)
                .maxRetries(0).build(), "claude-opus-5-5");
        assertThrows(ModelRefusalException.class, () -> service.infer(MESSAGES, RequestContext.begin("acme")));
    }

    @Test
    void azureOpenAiResponsesApi() {
        reply = """
                {"id":"resp_1","object":"response","created_at":0,"model":"gpt-5","status":"completed",
                 "output":[{"type":"message","id":"m1","role":"assistant","status":"completed",
                   "content":[{"type":"output_text","text":"S-1 was flagged.","annotations":[]}]}],
                 "parallel_tool_calls":true,"tool_choice":"auto","tools":[],
                 "usage":{"input_tokens":10,"output_tokens":4,"total_tokens":14,
                   "input_tokens_details":{"cached_tokens":0},"output_tokens_details":{"reasoning_tokens":0}}}""";
        var service = new OpenAiResponsesLlmService(OpenAIOkHttpClient.builder().apiKey("offline")
                .baseUrl(base + "/openai/v1").maxRetries(0).build(), "gpt-5");
        LlmResponse response = service.infer(MESSAGES, RequestContext.begin("acme"));
        assertEquals("S-1 was flagged.", response.text());
        assertEquals("/openai/v1/responses", paths.get(0));
        assertTrue(bodies.get(0).contains("\"instructions\":\"You are the settlement audit agent.\""));
        assertTrue(bodies.get(0).contains("\"store\":false"));
    }

    @Test
    void llamaStackChatCompletions() {
        reply = """
                {"id":"c1","object":"chat.completion","created":0,"model":"llama",
                 "choices":[{"index":0,"message":{"role":"assistant","content":"S-1 was flagged."},
                   "finish_reason":"stop"}],"usage":{"prompt_tokens":9,"completion_tokens":4,"total_tokens":13}}""";
        var service = new OpenAiChatLlmService(OpenAIOkHttpClient.builder().apiKey("none")
                .baseUrl(base + "/v1").maxRetries(0).build(), "meta.llama4-maverick-17b-instruct-v1:0");
        LlmResponse response = service.infer(MESSAGES, RequestContext.begin("acme"));
        assertEquals("S-1 was flagged.", response.text());
        assertEquals(13, response.tokensUsed());
        assertEquals("/v1/chat/completions", paths.get(0));
    }

    @Test
    void geminiGenerateContent() {
        reply = """
                {"candidates":[{"content":{"role":"model","parts":[{"text":"S-1 was flagged."}]},
                  "finishReason":"STOP","index":0}],
                 "usageMetadata":{"promptTokenCount":8,"candidatesTokenCount":4,"totalTokenCount":12}}""";
        Client client = Client.builder().apiKey("offline")
                .httpOptions(HttpOptions.builder().baseUrl(base).build()).build();
        LlmResponse response = new GeminiLlmService(client, "gemini-2.5-pro").infer(MESSAGES, RequestContext.begin("acme"));
        assertEquals("S-1 was flagged.", response.text());
        assertEquals(12, response.tokensUsed());
        assertTrue(paths.get(0).endsWith("gemini-2.5-pro:generateContent"), paths.get(0));
        assertTrue(bodies.get(0).contains("settlement audit agent"));
    }
}
