DEF INT16_MAXVALUE = 32767

from libc.stdint cimport int16_t, int32_t

from cpython cimport array
import array


cpdef int16_t saturate(double value):
    cdef int32_t ival = <int32_t>value
    if ival > INT16_MAXVALUE:
        return INT16_MAXVALUE
    if ival < -INT16_MAXVALUE:
        return -INT16_MAXVALUE
    return <int16_t>ival


cpdef calculate_panning(double pan, array.array mono, array.array stereo, int32_t want_frames):
    cdef int32_t i
    for i in range(want_frames):
            stereo.data.as_shorts[2 * i] = <int16_t>((-pan + 1) / 2 * mono.data.as_shorts[i])
            stereo.data.as_shorts[2 * i + 1] = <int16_t>((pan + 1) / 2 * mono.data.as_shorts[i])
