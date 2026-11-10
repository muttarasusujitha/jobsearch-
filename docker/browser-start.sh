#!/bin/sh
# Starts a virtual screen, a VNC server on it, the noVNC web viewer, then the browser service.
set -e

Xvfb :99 -screen 0 1440x900x24 -nolisten tcp &
sleep 1
fluxbox >/dev/null 2>&1 &

if [ -n "$VNC_PASSWORD" ]; then
  mkdir -p "$HOME/.vnc"
  x11vnc -storepasswd "$VNC_PASSWORD" "$HOME/.vnc/passwd" >/dev/null
  VNC_AUTH="-rfbauth $HOME/.vnc/passwd"
else
  VNC_AUTH="-nopw"
fi
# VNC only listens inside the container; noVNC (port 6080) is the way in.
x11vnc -display :99 -forever -shared -localhost -rfbport 5900 -quiet $VNC_AUTH &
websockify --web /usr/share/novnc 6080 localhost:5900 >/dev/null 2>&1 &

exec python -m services.browser
