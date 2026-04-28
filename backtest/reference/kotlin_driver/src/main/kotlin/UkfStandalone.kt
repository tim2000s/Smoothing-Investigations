/* Standalone UKF — algorithmically identical to UnscentedKalmanFilterPlugin.kt,
 * with AAPS framework dependencies (logger, persistence, RxBus, sensor-event listener)
 * stripped out. The math, constants, and per-step behavior are unchanged.
 *
 * Source of truth: ../../UnscentedKalmanFilterPlugin.kt
 *
 * Differences vs the original (all behaviorally inert):
 * - No @Inject/@Singleton — plain class.
 * - No SP-based persistence — `learnedR` lives only for the lifetime of the instance.
 * - No EventTherapyEventChange listener — sensor-change reset is exposed via a
 *   `requestReset()` method that callers (Main.kt) can call before processing.
 * - All `aapsLogger.*` calls removed.
 */
import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt
import java.util.concurrent.atomic.AtomicBoolean

data class GlucoseReading(
    val timestamp: Long,        // ms since epoch
    val value: Double,          // raw mg/dL
    var smoothed: Double = 0.0,
    var trendArrow: String = "NONE",
)

/* Per-reading trace row mirroring the Python trace exactly. */
data class TraceRow(
    val kotlin_idx: Int,
    val ts_ms: Long,
    val input_glucose: Double,
    val segment_idx: Int,
    val dt_min: Double,
    val x_pred_g: Double,
    val x_pred_r: Double,
    val P_pred_gg: Double,
    val P_pred_rr: Double,
    val P_pred_gr: Double,
    val innov: Double,
    val innov_var: Double,
    val chi2: Double,
    val is_outlier: Boolean,
    val R_live: Double,
    val R_eff: Double,
    val K_g: Double,
    val K_r: Double,
    val x_upd_g: Double,
    val x_upd_r: Double,
    val P_upd_gg: Double,
    val P_upd_rr: Double,
    val R_after: Double,
    val is_rts: Boolean,
    val output_glucose: Double,
    val output_rate: Double,
    val session_id: Int,
    val session_meas_n: Long,
    val session_outlier_n: Long,
)

class UkfStandalone {
    private val n = 2
    private val alpha = 0.1
    private val beta = 2.0
    private val kappa = 0.0
    private val lambda = alpha * alpha * (n + kappa) - n
    private val gamma = sqrt(n + lambda)

    private val Wm = DoubleArray(2 * n + 1)
    private val Wc = DoubleArray(2 * n + 1)

    private val Q = doubleArrayOf(1.0, 0.0, 0.0, 0.35)

    private val R_INIT = 25.0
    private val R_MIN = 16.0
    private val R_MAX = 225.0
    private val R_EFF_MAX = 400.0
    private val innovationWindow = 18
    private val CHI_SQUARED_THRESHOLD = 15.13
    private val OUTLIER_ABSOLUTE = 65.0
    private val MAX_GLUCOSE_VARIANCE = 400.0
    private val MAX_RATE_VARIANCE = 4.0
    private val INNOVATION_RESET_THRESHOLD = 12.0
    private val INNOVATION_VALIDATION_SAMPLES = 15
    private val MINOR_GAP_THRESHOLD = 7.0
    private val MAJOR_GAP_THRESHOLD = 60.0
    private val RATE_DECAY_TIME_CONSTANT = 30.0
    private val MILLIS_PER_MINUTE = 1000.0 * 60.0

    private fun rateDamp(dt: Double): Double = exp(-dt / RATE_DECAY_TIME_CONSTANT)

    private data class DataSegment(val startIdx: Int, val endIdx: Int)

    private data class FilterState(
        val x: DoubleArray, val P: DoubleArray,
        val xPred: DoubleArray, val PPred: DoubleArray,
        val dt: Double,
    )

    private var learnedR = R_INIT
    private val innovations = ArrayDeque<Double>(innovationWindow + 1)
    private val rawInnovationVariance = ArrayDeque<Double>(innovationWindow + 1)
    private val predVarHistory = ArrayDeque<Double>(innovationWindow + 1)
    private var lastProcessedTimestamp: Long = 0
    private var sensorSessionId: Int = 0
    private var sessionMeasurementCount: Long = 0
    private var sessionOutlierCount: Long = 0
    private var consecutiveOutliers = 0

    private val resetRequested = AtomicBoolean(false)

    /* Per-call trace collector. Cleared at the start of `smooth()`. */
    private val traceRows = mutableListOf<TraceRow>()

    init {
        Wm[0] = lambda / (n + lambda)
        Wc[0] = lambda / (n + lambda) + (1 - alpha * alpha + beta)
        val w = 1.0 / (2.0 * (n + lambda))
        for (i in 1 until 2 * n + 1) { Wm[i] = w; Wc[i] = w }
    }

    fun requestReset() { resetRequested.set(true) }

    fun smooth(data: MutableList<GlucoseReading>): List<TraceRow> {
        traceRows.clear()
        if (data.isEmpty()) return emptyList()
        try { smoothInternal(data) } catch (e: Exception) {
            for (r in data) { r.smoothed = max(r.value, 39.0); r.trendArrow = "NONE" }
        }
        return traceRows.toList()
    }

    private fun shouldResetLearning(currentTimestamp: Long): Boolean {
        if (resetRequested.getAndSet(false)) return true
        if (lastProcessedTimestamp == 0L) return true
        val diff = (currentTimestamp - lastProcessedTimestamp) / MILLIS_PER_MINUTE
        if (diff < 0) return true
        if (diff > 1440.0) return true
        if (innovations.size >= INNOVATION_VALIDATION_SAMPLES) {
            val avg = innovations.average()
            if (avg > INNOVATION_RESET_THRESHOLD) return true
        }
        return false
    }

    private fun resetLearning() {
        learnedR = R_INIT
        innovations.clear(); rawInnovationVariance.clear(); predVarHistory.clear()
        sensorSessionId++
        sessionMeasurementCount = 0; sessionOutlierCount = 0; consecutiveOutliers = 0
    }

    private fun findDataSegments(data: List<GlucoseReading>): List<DataSegment> {
        if (data.size < 2) return emptyList()
        val segments = mutableListOf<DataSegment>()
        var segmentStart = 0
        for (i in 0 until data.size - 1) {
            val timeDiff = (data[i].timestamp - data[i + 1].timestamp) / MILLIS_PER_MINUTE
            if (timeDiff > MAJOR_GAP_THRESHOLD || timeDiff < 2.0 || data[i].value == 38.0) {
                if (i - segmentStart >= 2) segments.add(DataSegment(segmentStart, i))
                segmentStart = i + 1
            }
        }
        if (data.size - segmentStart >= 2) segments.add(DataSegment(segmentStart, data.size - 1))
        return segments
    }

    private fun smoothInternal(data: MutableList<GlucoseReading>) {
        if (shouldResetLearning(data[0].timestamp)) resetLearning()
        val segments = findDataSegments(data)
        if (segments.isEmpty()) {
            for (r in data) { r.smoothed = max(r.value, 39.0); r.trendArrow = "NONE" }
            return
        }
        val previousTimestamp = lastProcessedTimestamp
        lastProcessedTimestamp = data[0].timestamp
        for ((idx, segment) in segments.withIndex()) {
            processSegment(data, segment.startIdx, segment.endIdx, previousTimestamp, idx)
        }
        for (i in data.indices) {
            if (data[i].smoothed == 0.0) { data[i].smoothed = max(data[i].value, 39.0); data[i].trendArrow = "NONE" }
        }
    }

    private fun processSegment(
        data: MutableList<GlucoseReading>,
        startIdx: Int, endIdx: Int,
        previousTimestamp: Long, segIdx: Int,
    ) {
        val segmentSize = endIdx - startIdx + 1
        if (segmentSize < 2) {
            data[startIdx].smoothed = max(data[startIdx].value, 39.0)
            data[startIdx].trendArrow = "NONE"
            return
        }

        val initialGlucose = data[endIdx].value
        var initialRate = 0.0
        if (segmentSize >= 2 && endIdx > 0) {
            val dt = (data[endIdx - 1].timestamp - data[endIdx].timestamp) / MILLIS_PER_MINUTE
            if (dt in 3.0..7.0) {
                initialRate = (data[endIdx - 1].value - data[endIdx].value) / dt
                initialRate = initialRate.coerceIn(-4.0, 4.0)
            }
        }

        val x = doubleArrayOf(initialGlucose, initialRate)
        val P = doubleArrayOf(16.0, 0.0, 0.0, 1.0)
        var R = learnedR

        val forwardStates = ArrayDeque<FilterState>(segmentSize)
        val forwardResults = DoubleArray(segmentSize)
        forwardResults[segmentSize - 1] = x[0]

        val pendingTraceRows = mutableListOf<Pair<Int, TraceRow>>()  // (kotlin_idx, row-without-rts-fixup)

        val recentSigns = ArrayDeque<Int>(3)

        for (i in (endIdx - 1) downTo startIdx) {
            val dt = (data[i].timestamp - data[i + 1].timestamp) / MILLIS_PER_MINUTE
            if (dt > MINOR_GAP_THRESHOLD && dt <= MAJOR_GAP_THRESHOLD) x[1] *= rateDamp(dt)
            P[0] = P[0].coerceIn(0.1, MAX_GLUCOSE_VARIANCE)
            P[3] = P[3].coerceIn(0.001, MAX_RATE_VARIANCE)

            val (xPredBase, PPredBase) = predict(x, P, Q, dt)
            val z = data[i].value
            val isNewData = data[i].timestamp > previousTimestamp

            if (z <= 38.0) {
                val sb = FilterState(x.copyOf(), P.copyOf(), xPredBase.copyOf(), PPredBase.copyOf(), dt)
                x[0] = xPredBase[0]; x[1] = xPredBase[1]
                P[0] = PPredBase[0]; P[1] = PPredBase[1]; P[2] = PPredBase[2]; P[3] = PPredBase[3]
                forwardResults[i - startIdx] = x[0]
                forwardStates.addFirst(sb)
                pendingTraceRows.add(i to TraceRow(
                    kotlin_idx = i, ts_ms = data[i].timestamp, input_glucose = z,
                    segment_idx = segIdx, dt_min = dt,
                    x_pred_g = xPredBase[0], x_pred_r = xPredBase[1],
                    P_pred_gg = PPredBase[0], P_pred_rr = PPredBase[3], P_pred_gr = PPredBase[1],
                    innov = Double.NaN, innov_var = Double.NaN, chi2 = Double.NaN, is_outlier = false,
                    R_live = R, R_eff = Double.NaN, K_g = Double.NaN, K_r = Double.NaN,
                    x_upd_g = x[0], x_upd_r = x[1], P_upd_gg = P[0], P_upd_rr = P[3],
                    R_after = R, is_rts = false, output_glucose = x[0], output_rate = x[1],
                    session_id = sensorSessionId, session_meas_n = sessionMeasurementCount,
                    session_outlier_n = sessionOutlierCount,
                ))
                continue
            }

            val innovation = z - xPredBase[0]
            val innovationVarianceRaw = PPredBase[0] + R
            val stdRaw = sqrt(innovationVarianceRaw)
            val normRaw = innovation / stdRaw

            val sign = when { normRaw > 0.0 -> 1; normRaw < 0.0 -> -1; else -> 0 }
            if (recentSigns.size == 3) recentSigns.removeLast()
            recentSigns.addFirst(if (abs(normRaw) > 2.0) sign else 0)
            val sameSignCount = if (sign == 0) 0 else recentSigns.count { it == sign }
            val qInflateAllowed = sameSignCount >= 2 && sign != 0
            val absn = abs(normRaw)

            val rScale = 1.0 + max(0.0, absn - 2.0)
            val R_eff = min(R * rScale, min(R + 100.0, R_EFF_MAX))

            val zScore = absn.coerceAtLeast(1.0)
            val qScale = if (qInflateAllowed) zScore.coerceIn(1.0, 3.0) else 1.0
            val tempQ = if (qScale > 1.0) {
                Q.copyOf().apply { this[0] = Q[0] * min(qScale, 2.0); this[3] = Q[3] * qScale }
            } else Q

            val (xPredEff, PPredEff) =
                if (qScale > 1.0) predict(x, P, tempQ, dt) else Pair(xPredBase, PPredBase)
            val sb = FilterState(x.copyOf(), P.copyOf(), xPredEff.copyOf(), PPredEff.copyOf(), dt)

            val innovationVarianceEff = PPredEff[0] + R_eff
            val mahalSqEff = (innovation * innovation) / innovationVarianceEff

            predVarHistory.addFirst(PPredEff[0])
            if (predVarHistory.size > innovationWindow) predVarHistory.removeLast()

            val K = update(xPredEff, PPredEff, z, R_eff, x, P)
            trackInnovation(innovation, innovationVarianceEff)

            val skipRUpdate = qInflateAllowed || absn > 3.0
            if (!skipRUpdate) R = adaptMeasurementNoise(R, innovations, rawInnovationVariance)

            val isOutlier = mahalSqEff > CHI_SQUARED_THRESHOLD || abs(innovation) > OUTLIER_ABSOLUTE
            if (isNewData) {
                sessionMeasurementCount++
                if (isOutlier) sessionOutlierCount++
            }

            forwardResults[i - startIdx] = x[0]
            forwardStates.addFirst(sb)

            pendingTraceRows.add(i to TraceRow(
                kotlin_idx = i, ts_ms = data[i].timestamp, input_glucose = z,
                segment_idx = segIdx, dt_min = dt,
                x_pred_g = xPredEff[0], x_pred_r = xPredEff[1],
                P_pred_gg = PPredEff[0], P_pred_rr = PPredEff[3], P_pred_gr = PPredEff[1],
                innov = innovation, innov_var = innovationVarianceEff, chi2 = mahalSqEff, is_outlier = isOutlier,
                R_live = R, R_eff = R_eff, K_g = K[0], K_r = K[1],
                x_upd_g = x[0], x_upd_r = x[1], P_upd_gg = P[0], P_upd_rr = P[3],
                R_after = R, is_rts = false, output_glucose = x[0], output_rate = x[1],
                session_id = sensorSessionId, session_meas_n = sessionMeasurementCount,
                session_outlier_n = sessionOutlierCount,
            ))
        }

        learnedR = R

        // RTS backward smoothing within segment
        val smoothedResults = forwardResults.copyOf()
        if (segmentSize >= 3 && forwardStates.isNotEmpty()) {
            val maxSmoothSteps = min(segmentSize - 1, forwardStates.size)
            val xSmooth = doubleArrayOf(forwardResults[0], x[1])
            for (i in 1..maxSmoothSteps) {
                val state = forwardStates[i - 1]
                val C = computeSmootherGain(state.P, state.PPred, state.dt)
                val dx0 = xSmooth[0] - state.xPred[0]
                val dx1 = xSmooth[1] - state.xPred[1]
                xSmooth[0] = forwardResults[i] + C[0] * dx0 + C[1] * dx1
                xSmooth[1] = state.x[1] + C[2] * dx0 + C[3] * dx1
                smoothedResults[i] = xSmooth[0]
            }
        }

        for (i in startIdx..endIdx) {
            val resultIdx = i - startIdx
            data[i].smoothed = max(smoothedResults[resultIdx], 39.0)
            data[i].trendArrow = if (i == startIdx) computeTrendArrow(x[1]) else "NONE"
        }

        // Patch RTS-modified outputs into the trace (replace the post-update value with the
        // RTS smoothed value for every reading except the oldest in the segment).
        for ((kIdx, row) in pendingTraceRows) {
            val resultIdx = kIdx - startIdx
            val rts = max(smoothedResults[resultIdx], 39.0)
            traceRows.add(row.copy(output_glucose = rts, is_rts = (kIdx != endIdx)))
        }
    }

    private fun trackInnovation(innovation: Double, variance: Double) {
        innovations.addFirst((innovation * innovation) / variance)
        rawInnovationVariance.addFirst(innovation * innovation)
        if (innovations.size > innovationWindow) innovations.removeLast()
        if (rawInnovationVariance.size > innovationWindow) rawInnovationVariance.removeLast()
    }

    private fun adaptMeasurementNoise(
        currentR: Double, innovations: ArrayDeque<Double>, rawSq: ArrayDeque<Double>,
    ): Double {
        if (innovations.size < 12 || predVarHistory.isEmpty()) return currentR
        fun trimmedMean(v: List<Double>, trim: Double = 0.20): Double {
            if (v.isEmpty()) return 0.0
            val s = v.sorted()
            val k = (s.size * trim).toInt().coerceAtMost((s.size - 1) / 2)
            val core = s.subList(k, s.size - k)
            return core.average()
        }
        val N = innovations.size
        val mRaw = trimmedMean(rawSq.take(N))
        val pyy = trimmedMean(predVarHistory.take(N))
        val RhatRaw = (mRaw - pyy).coerceAtLeast(R_MIN)
        val Rhat = RhatRaw.coerceIn(R_MIN, R_MAX)
        val diff = Rhat - currentR
        val goingUp = diff > 0.0
        val k = if (goingUp) 0.18 else 0.12
        val step = currentR + k * diff
        val upCap = if (goingUp) 1.20 else 1.00
        val dnCap = if (goingUp) 1.00 else 0.90
        val clamped = step.coerceIn(currentR * dnCap, currentR * upCap).coerceIn(R_MIN, R_MAX)
        val eta = 0.25
        return (1.0 - eta) * currentR + eta * clamped
    }

    private fun computeTrendArrow(rate: Double): String = when {
        rate > 2.0 -> "DOUBLE_UP"; rate > 1.0 -> "SINGLE_UP"; rate > 0.5 -> "FORTY_FIVE_UP"
        rate < -2.0 -> "DOUBLE_DOWN"; rate < -1.0 -> "SINGLE_DOWN"; rate < -0.5 -> "FORTY_FIVE_DOWN"
        else -> "FLAT"
    }

    private fun computeSmootherGain(P: DoubleArray, PPred: DoubleArray, dt: Double): DoubleArray {
        val damp = rateDamp(dt)
        val PFt00 = P[0] + P[1] * dt; val PFt01 = P[1] * damp
        val PFt10 = P[2] + P[3] * dt; val PFt11 = P[3] * damp
        val det = PPred[0] * PPred[3] - PPred[1] * PPred[2]
        if (abs(det) < 1e-10) return doubleArrayOf(0.0, 0.0, 0.0, 0.0)
        val inv00 = PPred[3] / det; val inv01 = -PPred[1] / det
        val inv10 = -PPred[2] / det; val inv11 = PPred[0] / det
        return doubleArrayOf(
            PFt00 * inv00 + PFt01 * inv10, PFt00 * inv01 + PFt01 * inv11,
            PFt10 * inv00 + PFt11 * inv10, PFt10 * inv01 + PFt11 * inv11,
        )
    }

    private fun predict(x: DoubleArray, P: DoubleArray, Q: DoubleArray, dt: Double): Pair<DoubleArray, DoubleArray> {
        val sigmaPoints = generateSigmaPoints(x, P)
        val sigmaPointsPred = Array(2 * n + 1) { DoubleArray(n) }
        val damp = rateDamp(dt)
        for (i in 0 until (2 * n + 1)) {
            sigmaPointsPred[i][0] = sigmaPoints[i][0] + sigmaPoints[i][1] * dt
            sigmaPointsPred[i][1] = sigmaPoints[i][1] * damp
        }
        val xPred = DoubleArray(n)
        for (i in 0 until (2 * n + 1)) { xPred[0] += Wm[i] * sigmaPointsPred[i][0]; xPred[1] += Wm[i] * sigmaPointsPred[i][1] }
        val PPred = DoubleArray(4)
        for (i in 0 until (2 * n + 1)) {
            val dx0 = sigmaPointsPred[i][0] - xPred[0]; val dx1 = sigmaPointsPred[i][1] - xPred[1]
            PPred[0] += Wc[i] * dx0 * dx0; PPred[1] += Wc[i] * dx0 * dx1
            PPred[2] += Wc[i] * dx1 * dx0; PPred[3] += Wc[i] * dx1 * dx1
        }
        val qScale = dt / 5.0
        PPred[0] += Q[0] * qScale; PPred[3] += Q[3] * qScale
        PPred[0] = max(PPred[0], 0.1); PPred[3] = max(PPred[3], 0.001)
        return Pair(xPred, PPred)
    }

    private fun update(xPred: DoubleArray, PPred: DoubleArray, z: Double, R: Double, x: DoubleArray, P: DoubleArray): DoubleArray {
        val sigmaPoints = generateSigmaPoints(xPred, PPred)
        val zSigma = DoubleArray(2 * n + 1)
        for (i in 0 until 2 * n + 1) zSigma[i] = sigmaPoints[i][0]
        var zPred = 0.0
        for (i in 0 until 2 * n + 1) zPred += Wm[i] * zSigma[i]
        var Pzz = 0.0
        for (i in 0 until 2 * n + 1) { val dz = zSigma[i] - zPred; Pzz += Wc[i] * dz * dz }
        Pzz += R
        if (Pzz < 1e-6) {
            x[0] = xPred[0]; x[1] = xPred[1]
            P[0] = PPred[0]; P[1] = PPred[1]; P[2] = PPred[2]; P[3] = PPred[3]
            return doubleArrayOf(0.0, 0.0)
        }
        val Pxz = DoubleArray(n)
        for (i in 0 until 2 * n + 1) {
            val dx0 = sigmaPoints[i][0] - xPred[0]; val dx1 = sigmaPoints[i][1] - xPred[1]
            val dz = zSigma[i] - zPred
            Pxz[0] += Wc[i] * dx0 * dz; Pxz[1] += Wc[i] * dx1 * dz
        }
        val K = doubleArrayOf(Pxz[0] / Pzz, Pxz[1] / Pzz)
        val innovation = z - zPred
        x[0] = xPred[0] + K[0] * innovation
        x[1] = xPred[1] + K[1] * innovation
        x[1] = x[1].coerceIn(-4.0, 4.0)
        P[0] = PPred[0] - K[0] * Pzz * K[0]; P[1] = PPred[1] - K[0] * Pzz * K[1]
        P[2] = PPred[2] - K[1] * Pzz * K[0]; P[3] = PPred[3] - K[1] * Pzz * K[1]
        P[0] = max(P[0], 0.1); P[3] = max(P[3], 0.001)
        return K
    }

    private fun generateSigmaPoints(x: DoubleArray, P: DoubleArray): Array<DoubleArray> {
        val sp = Array(2 * n + 1) { DoubleArray(n) }
        val sqrtP = matrixSqrt2x2(P)
        sp[0][0] = x[0]; sp[0][1] = x[1]
        for (i in 0 until n) {
            sp[i + 1][0] = x[0] + gamma * sqrtP[i * 2 + 0]
            sp[i + 1][1] = x[1] + gamma * sqrtP[i * 2 + 1]
            sp[i + 1 + n][0] = x[0] - gamma * sqrtP[i * 2 + 0]
            sp[i + 1 + n][1] = x[1] - gamma * sqrtP[i * 2 + 1]
        }
        return sp
    }

    private fun matrixSqrt2x2(P: DoubleArray): DoubleArray {
        val a = P[0]; val b = (P[1] + P[2]) / 2.0; val d = P[3]
        val l11 = sqrt(max(a, 1e-9))
        val l21 = b / l11
        val discriminant = d - l21 * l21
        if (discriminant < -1e-9) return doubleArrayOf(sqrt(max(a, 0.1)), 0.0, 0.0, sqrt(max(d, 0.01)))
        val l22 = sqrt(max(discriminant, 1e-9))
        return doubleArrayOf(l11, l21, 0.0, l22)
    }
}
