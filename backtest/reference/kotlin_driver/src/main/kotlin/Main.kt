/* Driver: read inputs.json, run the three AAPS smoothers (AvgSmoothingPlugin,
 * ExponentialSmoothingPlugin, UnscentedKalmanFilterPlugin) on each fixture, and
 * write per-smoother JSON outputs that the Python parity tests consume.
 *
 * For each algorithm and each fixture, two output forms are emitted:
 *   - "<algo>.json"        — production batch mode: one smooth() call over
 *                             the full fixture, capture the smoothed array.
 *   - "<algo>_online.json" — online mode: for each leading-edge index t, call
 *                             smooth() on data[t-W+1 .. t] (newest-first list)
 *                             and capture the smoothed value for the leading
 *                             edge (data[0] in the call). W is smoother-
 *                             specific: AAPS Avg always uses the full prefix
 *                             (the algorithm's smoother window is fixed at 3),
 *                             AAPS Exp uses 24, UKF uses 36.
 *
 * Important: the fixture inputs are emitted by Python in chronological order
 * (oldest first). AAPS smoothers expect newest-first (data[0] = newest). We
 * reverse the input here before each smooth() call so the Kotlin algorithm
 * sees the same data layout it sees in production.
 */
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.module.kotlin.registerKotlinModule
import java.io.File

private const val EXP_WINDOW = 24
private const val UKF_WINDOW = 36   // ~3 hours at 5-min cadence

fun runAvgBatch(readings: MutableList<GlucoseReading>): List<Map<String, Any>> {
    AvgSmoothingStandalone().smooth(readings)
    val n = readings.size
    return readings.mapIndexed { kIdx, r ->
        mapOf(
            "kotlin_idx" to kIdx,
            "reading_idx" to (n - 1 - kIdx),
            "ts_ms" to r.timestamp,
            "input_glucose" to r.value,
            "smoothed_glucose" to r.smoothed,
        )
    }.sortedBy { it["reading_idx"] as Int }
}

fun runExpBatch(readings: MutableList<GlucoseReading>): List<Map<String, Any>> {
    ExponentialSmoothingStandalone().smooth(readings)
    val n = readings.size
    return readings.mapIndexed { kIdx, r ->
        mapOf(
            "kotlin_idx" to kIdx,
            "reading_idx" to (n - 1 - kIdx),
            "ts_ms" to r.timestamp,
            "input_glucose" to r.value,
            "smoothed_glucose" to r.smoothed,
        )
    }.sortedBy { it["reading_idx"] as Int }
}

/** Online mode for AAPS Avg / Exp: at each chronological index t, call smooth()
 *  with data [t-W+1 .. t] in newest-first order, and capture the leading-edge
 *  smoothed value (= smoothed for data[0] within that call = chronological t).
 *
 *  For AAPS Avg this is interesting because data[0] (newest) is NOT smoothed by
 *  the algorithm; we record its raw value as the leading-edge output (which is
 *  what the production loop reads when it asks for "the current reading"
 *  smoothed). The reading at t-1 in the sliding window does get smoothed and
 *  matches the batch result one step later.
 */
fun runOnlineSliding(
    smootherFn: (MutableList<GlucoseReading>) -> Unit,
    chronological: List<GlucoseReading>,
    windowSize: Int,
): List<Map<String, Any>> {
    val n = chronological.size
    val out = mutableListOf<Map<String, Any>>()
    for (t in 0 until n) {
        val lo = maxOf(0, t - windowSize + 1)
        // Slice [lo..t] chronologically, then reverse to newest-first.
        val window = chronological.subList(lo, t + 1).reversed().map {
            GlucoseReading(it.timestamp, it.value)
        }.toMutableList()
        if (window.size < 2) {
            // Not enough data to smooth; emit raw.
            out.add(mapOf(
                "reading_idx" to t,
                "ts_ms" to chronological[t].timestamp,
                "input_glucose" to chronological[t].value,
                "smoothed_glucose" to chronological[t].value,
            ))
            continue
        }
        smootherFn(window)
        // Leading-edge value at chronological t = window[0] (newest in the call).
        // For AAPS Avg, smoothed will be 0.0 (the algorithm doesn't touch the
        // newest), in which case we fall back to the raw value because that is
        // what production loop sees at decision time.
        val leadingEdge = if (window[0].smoothed > 0.0) window[0].smoothed else window[0].value
        out.add(mapOf(
            "reading_idx" to t,
            "ts_ms" to chronological[t].timestamp,
            "input_glucose" to chronological[t].value,
            "smoothed_glucose" to leadingEdge,
        ))
    }
    return out
}

fun main(args: Array<String>) {
    if (args.size < 2) {
        System.err.println("usage: smoother-driver <inputs.json> <out_dir>")
        kotlin.system.exitProcess(1)
    }
    val inputsPath = args[0]
    val outDir = File(args[1]).apply { mkdirs() }

    val mapper = ObjectMapper().registerKotlinModule()
    val inputs = mapper.readTree(File(inputsPath))

    val avgResults = mutableMapOf<String, Any>()
    val expResults = mutableMapOf<String, Any>()
    val ukfResults = mutableMapOf<String, Any>()
    val avgOnline = mutableMapOf<String, Any>()
    val expOnline = mutableMapOf<String, Any>()
    val ukfOnline = mutableMapOf<String, Any>()

    inputs.fields().forEach { (name, fixture) ->
        val tsMs = fixture.get("ts_ms").map { it.asLong() }
        val glucose = fixture.get("glucose").map { it.asDouble() }
        check(tsMs.size == glucose.size) { "ts_ms / glucose length mismatch for $name" }

        // Build chronological list (oldest first) once.
        val chrono: List<GlucoseReading> = (tsMs.indices).map { GlucoseReading(tsMs[it], glucose[it]) }

        // ---- AAPS Avg batch ----
        val avgInput = chrono.reversed().map { GlucoseReading(it.timestamp, it.value) }.toMutableList()
        avgResults[name] = mapOf(
            "n" to chrono.size,
            "trace" to runAvgBatch(avgInput),
        )
        // ---- AAPS Avg online ----
        avgOnline[name] = mapOf(
            "n" to chrono.size,
            "trace" to runOnlineSliding(
                { AvgSmoothingStandalone().smooth(it) },
                chrono,
                windowSize = chrono.size,   // Avg uses 3-point window inside; full prefix is fine
            ),
        )

        // ---- AAPS Exp batch ----
        val expInput = chrono.reversed().map { GlucoseReading(it.timestamp, it.value) }.toMutableList()
        expResults[name] = mapOf(
            "n" to chrono.size,
            "trace" to runExpBatch(expInput),
        )
        // ---- AAPS Exp online ----
        expOnline[name] = mapOf(
            "n" to chrono.size,
            "trace" to runOnlineSliding(
                { ExponentialSmoothingStandalone().smooth(it) },
                chrono,
                windowSize = EXP_WINDOW,
            ),
        )

        // ---- UKF batch (existing behaviour) ----
        val ukfReadings = chrono.reversed().map { GlucoseReading(it.timestamp, it.value) }.toMutableList()
        val ukf = UkfStandalone()
        val trace = ukf.smooth(ukfReadings)
        val n = chrono.size
        val byReading = trace.map { row ->
            mapOf(
                "reading_idx" to (n - 1 - row.kotlin_idx),
                "kotlin_idx" to row.kotlin_idx,
                "ts_ms" to row.ts_ms,
                "input_glucose" to row.input_glucose,
                "segment_idx" to row.segment_idx,
                "dt_min" to row.dt_min,
                "x_pred_g" to row.x_pred_g,
                "x_pred_r" to row.x_pred_r,
                "P_pred_gg" to row.P_pred_gg,
                "P_pred_rr" to row.P_pred_rr,
                "P_pred_gr" to row.P_pred_gr,
                "innov" to row.innov,
                "innov_var" to row.innov_var,
                "chi2" to row.chi2,
                "is_outlier" to row.is_outlier,
                "R_live" to row.R_live,
                "R_eff" to row.R_eff,
                "K_g" to row.K_g,
                "K_r" to row.K_r,
                "x_upd_g" to row.x_upd_g,
                "x_upd_r" to row.x_upd_r,
                "P_upd_gg" to row.P_upd_gg,
                "P_upd_rr" to row.P_upd_rr,
                "R_after" to row.R_after,
                "is_rts" to row.is_rts,
                "output_glucose" to row.output_glucose,
                "output_rate" to row.output_rate,
                "session_id" to row.session_id,
                "session_meas_n" to row.session_meas_n,
                "session_outlier_n" to row.session_outlier_n,
            )
        }.sortedBy { it["reading_idx"] as Int }
        ukfResults[name] = mapOf("n" to n, "n_trace" to byReading.size, "trace" to byReading)

        // ---- UKF online ----
        ukfOnline[name] = mapOf(
            "n" to chrono.size,
            "trace" to runOnlineSliding(
                { UkfStandalone().smooth(it) },
                chrono,
                windowSize = UKF_WINDOW,
            ),
        )
    }

    val pretty = mapper.writerWithDefaultPrettyPrinter()
    pretty.writeValue(File(outDir, "aaps_average.json"), avgResults)
    pretty.writeValue(File(outDir, "aaps_average_online.json"), avgOnline)
    pretty.writeValue(File(outDir, "aaps_exponential.json"), expResults)
    pretty.writeValue(File(outDir, "aaps_exponential_online.json"), expOnline)
    pretty.writeValue(File(outDir, "ukf.json"), ukfResults)
    pretty.writeValue(File(outDir, "ukf_online.json"), ukfOnline)
    println("Wrote 6 reference files to ${outDir.absolutePath}")
}
