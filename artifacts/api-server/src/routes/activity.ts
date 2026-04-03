import { Router, type Request, type Response } from "express";
import { activityBus } from "./trading.js";

const router = Router();

router.get("/stream", (req: Request, res: Response) => {
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.setHeader("X-Accel-Buffering", "no");
  res.flushHeaders();

  const send = (data: string) => {
    res.write(`data: ${data}\n\n`);
  };

  send(JSON.stringify({ type: "connected", msg: "Activity stream connected", ts: new Date().toISOString() }));

  const heartbeat = setInterval(() => {
    res.write(": ping\n\n");
  }, 15000);

  activityBus.listeners.push(send);

  req.on("close", () => {
    clearInterval(heartbeat);
    activityBus.listeners = activityBus.listeners.filter((fn) => fn !== send);
  });
});

export default router;
