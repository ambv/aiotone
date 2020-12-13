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


cdef class Envelope:
    cdef int a  # in number of samples
    cdef int d  # in number of samples
    cdef double s  # 0.0 - 1.0; relative volume
    cdef int r  # in number of samples

    cdef bint released  # bint: Cython boolean int
    cdef int samples_since_reset
    cdef double current_value

    def __init__(self, int a, int d, double s, int r):
        self.a = a
        self.d = d
        self.s = s
        self.r = r
        self.released = False
        self.samples_since_reset = -1  # not flowing
        self.current_value = 0.0

    def reset(self):
        self.released = False
        self.samples_since_reset = 0
        self.current_value = 0.0

    def release(self):
        self.released = True

    def advance(self):
        cdef double envelope = self.current_value
        cdef int samples_since_reset = self.samples_since_reset
        cdef int a = self.a or 1
        cdef int d = self.d
        cdef double s = self.s
        cdef int r = self.r or 1

        if samples_since_reset == -1:
            return 0.0

        samples_since_reset += 1
        # Release
        if self.released:
            if envelope > 0:
                envelope -= 1 / r
            else:
                envelope = 0.0
                samples_since_reset = -1
        # Attack
        elif samples_since_reset <= a:
            envelope += 1 / a
        # Decay
        elif samples_since_reset <= a + d:
            envelope -= (1 - s) / d
        # Sustain
        elif s:
            envelope = s
        # Silence
        else:
            envelope = 0.0
            samples_since_reset = -1

        self.samples_since_reset = samples_since_reset
        self.current_value = envelope
        return envelope

    def is_silent(self):
        return self.samples_since_reset < 0 and self.current_value == 0