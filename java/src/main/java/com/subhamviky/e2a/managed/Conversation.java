package com.subhamviky.e2a.managed;

import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/** The e2a-core message list ({role, content}) split into what every provider needs. */
record Conversation(String system, List<Map<String, String>> turns) {

    static Conversation of(List<Map<String, String>> messages) {
        String system = messages.stream().filter(m -> "system".equals(m.get("role")))
                .map(m -> m.getOrDefault("content", "")).collect(Collectors.joining("\n\n"));
        List<Map<String, String>> turns = messages.stream().filter(m -> !"system".equals(m.get("role"))).toList();
        return new Conversation(system, turns);
    }

    static boolean isAssistant(Map<String, String> m) {
        return "assistant".equals(m.get("role"));
    }
}
