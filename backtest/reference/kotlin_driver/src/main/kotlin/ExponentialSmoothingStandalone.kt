/* Standalone port of ExponentialSmoothingPlugin.kt — algorithmically identical,
 * AAPS framework dependencies stripped. The TSUNAMI dual-EMA blend is preserved
 * verbatim (1st-order EMA with α = 0.5 + 2nd-order trended EMA with α = 0.4,
 * β = 1.0, blended at o1_weight = 0.4 / o2_weight = 0.6).
 *
 * Source of truth: ../../ExponentialSmoothingPlugin.kt (verbatim copy of the
 * upstream AAPS plugin file).
 */
import kotlin.math.max
import kotlin.math.round

class ExponentialSmoothingStandalone {
    @Suppress("LocalVariableName")
    fun smooth(data: MutableList<GlucoseReading>): MutableList<GlucoseReading> {
        val sizeRecords = data.size
        val o1_sBG: ArrayList<Double> = ArrayList()
        val o2_sBG: ArrayList<Double> = ArrayList()
        val o2_sD: ArrayList<Double> = ArrayList()
        val ssBG: ArrayList<Double> = ArrayList()
        var windowSize = data.size
        val o1_weight = 0.4
        val o1_a = 0.5
        val o2_a = 0.4
        val o2_b = 1.0
        var insufficientSmoothingData = false

        if (sizeRecords <= windowSize) {
            windowSize = (sizeRecords - 1).coerceAtLeast(0)
        }

        for (i in 0 until windowSize) {
            if (round((data[i].timestamp - data[i + 1].timestamp) / (1000.0 * 60)) >= 12) {
                windowSize = i + 1
                break
            } else if (data[i].value == 38.0) {
                windowSize = i
                break
            }
        }

        o1_sBG.clear()
        if (windowSize >= 4) {
            o1_sBG.add(data[windowSize - 1].value)
            for (i in 0 until windowSize) {
                o1_sBG.add(0, o1_sBG[0] + o1_a * (data[windowSize - 1 - i].value - o1_sBG[0]))
            }
        } else {
            insufficientSmoothingData = true
        }

        if (windowSize >= 4) {
            o2_sBG.add(data[windowSize - 1].value)
            o2_sD.add(data[windowSize - 2].value - data[windowSize - 1].value)
            for (i in 0 until windowSize - 1) {
                o2_sBG.add(0, o2_a * data[windowSize - 2 - i].value + (1 - o2_a) * (o2_sBG[0] + o2_sD[0]))
                o2_sD.add(0, o2_b * (o2_sBG[0] - o2_sBG[1]) + (1 - o2_b) * o2_sD[0])
            }
        } else {
            insufficientSmoothingData = true
        }

        if (!insufficientSmoothingData) {
            for (i in o2_sBG.indices) {
                ssBG.add(o1_weight * o1_sBG[i] + (1 - o1_weight) * o2_sBG[i])
            }
            for (i in 0 until minOf(ssBG.size, data.size)) {
                data[i].smoothed = max(round(ssBG[i]), 39.0)
            }
        } else {
            for (i in 0 until data.size) {
                data[i].smoothed = max(data[i].value, 39.0)
            }
        }

        return data
    }
}
