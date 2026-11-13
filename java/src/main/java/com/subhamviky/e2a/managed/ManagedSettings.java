package com.subhamviky.e2a.managed;

import com.subhamviky.e2a.core.RequestContext;

/**
 * E2A's resolution order for the Java side: a request setting (the per-call value,
 * {@code ctx.withSettings(Map.of("reasoning_effort", "high"))}) wins over the
 * environment, which wins over the profile default. Same keys as the Python registry.
 */
public final class ManagedSettings {

    private ManagedSettings() {
    }

    public static String get(RequestContext ctx, String key, String env, String fallback) {
        Object perCall = ctx == null ? null : ctx.setting(key, null);
        if (perCall != null) {
            return String.valueOf(perCall);
        }
        String fromEnv = env == null ? null : System.getenv(env);
        return fromEnv != null && !fromEnv.isBlank() ? fromEnv : fallback;
    }

    public static int getInt(RequestContext ctx, String key, String env, int fallback) {
        return Integer.parseInt(get(ctx, key, env, Integer.toString(fallback)));
    }
}
