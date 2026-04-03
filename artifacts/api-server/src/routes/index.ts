import { Router, type IRouter } from "express";
import healthRouter from "./health.js";
import tradingRouter from "./trading.js";
import activityRouter from "./activity.js";
import settingsRouter from "./settings.js";

const router: IRouter = Router();

router.use(healthRouter);
router.use("/bot", tradingRouter);
router.use("/activity", activityRouter);
router.use("/settings", settingsRouter);

export default router;
