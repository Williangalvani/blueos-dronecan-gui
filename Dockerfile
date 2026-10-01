FROM python:3.11-slim-bookworm

WORKDIR /app
COPY requirements.txt .
# No armv7 wheel for pymavlink. The C indexer is only for dataflash logs, which this UI does not use.
# websockets is the library uvicorn needs to accept /api/ws. Without it the handshake is a 404.
RUN PYMAVLINK_FAST_INDEX=0 pip install --no-cache-dir -r requirements.txt
COPY app app
COPY static static
RUN mkdir -p /data

ENV PYTHONUNBUFFERED=1
ENV PORT=80
ENV DRONECAN_MAVLINK=udpout:host.docker.internal:14550
ENV DRONECAN_DATA=/data

LABEL version="1.0.0"
LABEL permissions='{\
  "ExposedPorts": {"80/tcp": {}},\
  "HostConfig": {\
    "ExtraHosts": ["host.docker.internal:host-gateway"],\
    "Binds": ["/usr/blueos/extensions/dronecan-gui:/data:rw"],\
    "PortBindings": {"80/tcp": [{"HostPort": ""}]}\
  }\
}'
LABEL authors='[{"name": "Willian Galvani", "email": "williangalvani@gmail.com"}]'
LABEL company='{"about": "DroneCAN GUI for BlueOS", "name": "Willian Galvani", "email": "williangalvani@gmail.com"}'
LABEL type="tool"
LABEL tags='["dronecan", "can"]'
LABEL readme="https://raw.githubusercontent.com/dronecan/gui_tool/master/README.md"
LABEL links='{"website": "https://dronecan.github.io/GUI_Tool/Overview/", "github": "https://github.com/dronecan/gui_tool"}'

EXPOSE 80
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-80}"]
