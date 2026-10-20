/**
 * Live metrics + command WebSocket client for Cinema / 3rd-party pages.
 *
 * Usage:
 *   const ws = connectStreamWs({
 *     onMetrics: (data) => {},
 *     onStatus: (id, data) => {},
 *     onBuffer: (id, data) => {},
 *     onActivity: (data) => {},
 *   });
 *   ws.subscribe(downloadId);
 *   ws.cmd({ cmd: "boost", download_id: id });
 */

export function connectStreamWs(handlers = {}) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${location.host}/api/v1/ws`;
  let socket = null;
  let closed = false;
  let retryMs = 800;
  const pending = [];

  function send(obj) {
    const raw = JSON.stringify(obj);
    if (socket && socket.readyState === WebSocket.OPEN) socket.send(raw);
    else pending.push(raw);
  }

  function connect() {
    if (closed) return;
    socket = new WebSocket(url);
    socket.addEventListener("open", () => {
      retryMs = 800;
      while (pending.length) socket.send(pending.shift());
      handlers.onOpen?.();
    });
    socket.addEventListener("message", (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      switch (msg.type) {
        case "hello":
          handlers.onHello?.(msg);
          break;
        case "metrics":
          handlers.onMetrics?.(msg.data);
          break;
        case "status":
          handlers.onStatus?.(msg.download_id, msg.data);
          break;
        case "buffer":
          handlers.onBuffer?.(msg.download_id, msg.data);
          break;
        case "activity":
          handlers.onActivity?.(msg.data);
          break;
        case "ack":
          handlers.onAck?.(msg);
          break;
        case "error":
          handlers.onError?.(msg.error || "ws error");
          break;
        default:
          handlers.onMessage?.(msg);
      }
    });
    socket.addEventListener("close", () => {
      handlers.onClose?.();
      if (closed) return;
      setTimeout(connect, retryMs);
      retryMs = Math.min(8000, retryMs * 1.6);
    });
    socket.addEventListener("error", () => {
      /* close handler reconnects */
    });
  }

  connect();

  return {
    subscribe(downloadId) {
      send({ cmd: "subscribe", download_id: downloadId });
    },
    unsubscribe() {
      send({ cmd: "unsubscribe" });
    },
    cmd(payload) {
      send(payload);
    },
    close() {
      closed = true;
      try {
        socket?.close();
      } catch {
        /* ignore */
      }
    },
  };
}
