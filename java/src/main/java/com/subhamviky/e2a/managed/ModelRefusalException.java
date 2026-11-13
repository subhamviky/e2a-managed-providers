package com.subhamviky.e2a.managed;

/** The managed model declined (a provider safety decision): never treated as an answer. */
public class ModelRefusalException extends RuntimeException {

    public ModelRefusalException(String provider, String model) {
        super(provider + " model " + model + " declined the request");
    }
}
