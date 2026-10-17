# Bakes the model into the image so the running container never downloads anything.
FROM ollama/ollama:0.40.6
ARG LLM_MODEL=llama3.2:1b
RUN ollama serve >/tmp/ollama.log 2>&1 & \
    for i in $(seq 1 30); do ollama list >/dev/null 2>&1 && break; sleep 1; done && \
    ollama pull "$LLM_MODEL" && \
    kill %1
