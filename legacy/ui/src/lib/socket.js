/**
 * Thin WebSocket wrapper. Knows nothing about event schemas or React --
 * it only opens a socket, hands parsed JSON messages to a callback, and
 * closes safely. core/events.py's Event.to_dict() always produces a JSON
 * object with an "event_type" field; a message that fails to parse as
 * JSON is logged and dropped rather than thrown, so one malformed frame
 * can't take down the whole connection.
 */
export function createEventSocket({ url, onOpen, onClose, onError, onMessage }) {
  const ws = new WebSocket(url);

  ws.addEventListener("open", (event) => {
    onOpen?.(event);
  });

  ws.addEventListener("close", (event) => {
    onClose?.(event);
  });

  ws.addEventListener("error", (event) => {
    onError?.(event);
  });

  ws.addEventListener("message", (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn("[triage-line] dropped non-JSON WebSocket message:", event.data);
      return;
    }
    onMessage?.(payload);
  });

  return {
    close: () => {
      // A socket still in CONNECTING state can't be closed cleanly on all
      // browsers without a benign error; guard on readyState defensively.
      if (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN) {
        ws.close();
      }
    },
    raw: ws,
  };
}
