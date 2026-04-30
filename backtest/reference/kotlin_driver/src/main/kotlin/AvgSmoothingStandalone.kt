/* Standalone port of AvgSmoothingPlugin.kt — algorithmically identical, AAPS
 * framework dependencies (PluginBase, AAPSLogger, ResourceHelper, dependency
 * injection) stripped out so the algorithm can run from a plain Kotlin driver.
 *
 * Source of truth: ../../AvgSmoothingPlugin.kt (verbatim copy of the upstream
 * AAPS plugin file at /Users/timstreet/StudioProjects/AndroidAPS/plugins/
 * smoothing/src/main/kotlin/app/aaps/plugins/smoothing/AvgSmoothingPlugin.kt).
 *
 * The smooth() method is bit-identical to the production code: 3-point central
 * window over the input list (newest-first per AAPS convention), with the
 * newest and oldest readings left unsmoothed. Skips readings that are out of
 * range (≤ 39 or ≥ 401 mg/dL) or whose timestamps are not evenly spaced
 * within ±30 s.
 */
import kotlin.math.abs

class AvgSmoothingStandalone {
    fun smooth(data: MutableList<GlucoseReading>): MutableList<GlucoseReading> {
        if (data.lastIndex < 4) return data
        for (i in data.lastIndex - 1 downTo 1) {
            if (isValid(data[i].value) && isValid(data[i - 1].value) && isValid(data[i + 1].value)
                && abs(data[i].timestamp - data[i - 1].timestamp - (data[i + 1].timestamp - data[i].timestamp)) < 30_000L
            ) {
                data[i].smoothed = (data[i - 1].value + data[i].value + data[i + 1].value) / 3.0
            }
        }
        return data
    }

    private fun isValid(n: Double): Boolean = n > 39 && n < 401
}
