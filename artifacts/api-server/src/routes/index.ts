import { Router, type IRouter } from "express";
import healthRouter from "./health";
import tradingRouter from "./trading";
import activityRouter from "./activity";

const router: IRouter = Router();

router.use(healthRouter);
router.use("/bot", tradingRouter);
router.use("/activity", activityRouter);

export default router;
