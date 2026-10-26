# SongSpec Analyzer service. CPU image by default; for a GPU host, build with
#   --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu130
# Python 3.12: the tempo engine runs on BPM Fixer's tested pins (service/requirements.txt), which need 3.12.
# NOT YET BUILT: Docker is not installed on the development PC (see PROGRESS.md).
FROM python:3.12-slim

ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libchromaprint-tools build-essential cmake \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY service/requirements.txt service/requirements.txt
RUN pip install --no-cache-dir torch==2.14.1 --index-url ${TORCH_INDEX} \
    && pip install --no-cache-dir -r service/requirements.txt pybind11
COPY service service
COPY contract contract
COPY client client
COPY demo demo
COPY tools tools
RUN pip install --no-cache-dir -e "service[redis]" \
    && python tools/build_native.py \
    && rm -rf build
# model weights: mounted into /app/service/models (compose.yaml), or baked in at build time when the build has network access:
#   RUN python tools/fetch_models.py
ENV SONGSPEC_DATA_DIR=/data OMP_NUM_THREADS=4
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "songspec_analyzer.api.app:app", "--app-dir", "service", "--host", "0.0.0.0", "--port", "8000"]
