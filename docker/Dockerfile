# SongSpec Analyzer service. CPU image by default; for a GPU host, build with
#   --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu124
FROM python:3.11-slim

ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libchromaprint-tools build-essential cmake \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY service/pyproject.toml service/pyproject.toml
RUN pip install --no-cache-dir torch --index-url ${TORCH_INDEX}
COPY service service
COPY contract contract
COPY client client
COPY demo demo
COPY tools tools
RUN pip install --no-cache-dir -e "service[ml,redis]"

ENV SONGSPEC_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "songspec_analyzer.api.app:app", "--app-dir", "service", "--host", "0.0.0.0", "--port", "8000"]
