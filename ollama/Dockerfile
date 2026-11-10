FROM ollama/ollama:0.5.4

# Los modelos quedan horneados en la imagen (no en el volumen `ollama-modelos`): el corrector
# borra los volúmenes entre escenario y escenario (`docker compose down -v`), así que cualquier
# descarga en el arranque del contenedor compite por el poco tiempo real que dura cada escenario
# (~1-2 minutos) contra el resto de la planta levantando. Medido en vivo: bajar qwen2.5:1.5b solo
# (986 MB) tardó más de 1 minuto — casi todo el escenario, dejando al agente sin tiempo para
# procesar ninguna nota. Horneando el modelo en el build, el contenedor arranca con el modelo ya
# listo, sin depender de la red en el camino crítico de la corrección.
ARG LLM_MODELO_EXTRACCION=qwen2.5:1.5b
ARG RAG_MODELO_EMBEDDINGS=nomic-embed-text

RUN ollama serve & \
    SERVER_PID=$! && \
    sleep 3 && \
    OLLAMA_HOST=127.0.0.1:11434 ollama pull "$LLM_MODELO_EXTRACCION" && \
    OLLAMA_HOST=127.0.0.1:11434 ollama pull "$RAG_MODELO_EMBEDDINGS" && \
    kill "$SERVER_PID"

ENTRYPOINT ["ollama", "serve"]
