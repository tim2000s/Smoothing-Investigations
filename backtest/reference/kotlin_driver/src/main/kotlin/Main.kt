/* Driver: read inputs.json, run UkfStandalone on each fixture, write
 * `<out_dir>/ukf.json` with one entry per fixture containing the trace.
 *
 * The Python parity test loads the same inputs.json, runs the Python UKF, and
 * asserts each (kotlin_idx, field) matches within tolerance.
 *
 * Important: the Python UKF processes chronological order (oldest first),
 * the Kotlin code processes newest-first. We feed the Kotlin code the
 * fixture data in newest-first order (reverse), exactly mirroring how the
 * Python port flips internally.
 */
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.module.kotlin.registerKotlinModule
import java.io.File

fun main(args: Array<String>) {
    if (args.size < 2) {
        System.err.println("usage: ukf-driver <inputs.json> <out_dir>")
        kotlin.system.exitProcess(1)
    }
    val inputsPath = args[0]
    val outDir = File(args[1]).apply { mkdirs() }

    val mapper = ObjectMapper().registerKotlinModule()
    val inputs = mapper.readTree(File(inputsPath))

    val results = mutableMapOf<String, Any>()

    inputs.fields().forEach { (name, fixture) ->
        val tsMs = fixture.get("ts_ms").map { it.asLong() }
        val glucose = fixture.get("glucose").map { it.asDouble() }
        check(tsMs.size == glucose.size) { "ts_ms / glucose length mismatch" }

        // Build Kotlin-style newest-first list.
        val readings = (tsMs.indices).reversed().map { i ->
            GlucoseReading(timestamp = tsMs[i], value = glucose[i])
        }.toMutableList()

        val ukf = UkfStandalone()
        val trace = ukf.smooth(readings)

        // Re-emit results sorted by chronological reading_idx so the Python comparator
        // can join on it directly (reading_idx = (n-1) - kotlin_idx).
        val n = readings.size
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

        results[name] = mapOf(
            "n" to n,
            "n_trace" to byReading.size,
            "trace" to byReading,
        )
    }

    val outFile = File(outDir, "ukf.json")
    mapper.writerWithDefaultPrettyPrinter().writeValue(outFile, results)
    println("Wrote ${outFile.absolutePath} (${results.size} fixtures)")
}
