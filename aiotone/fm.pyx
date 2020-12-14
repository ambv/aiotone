DEF INT16_MAXVALUE = 32767
DEF MAX_BUFFER = 2400  # 5 ms at 48000 Hz

cimport cython
from libc.stdint cimport int16_t, int32_t
from libc.math cimport floor

from cpython cimport array
import array


cpdef int16_t saturate(double value):
    """Constrain `value` between -INT16_MAXVALUE and INT16_MAXVALUE."""
    cdef int32_t ival = <int32_t>value
    if ival > INT16_MAXVALUE:
        return INT16_MAXVALUE
    if ival < -INT16_MAXVALUE:
        return -INT16_MAXVALUE
    return <int16_t>ival


cpdef calculate_panning(double pan, array.array mono, array.array stereo, int32_t want_frames):
    """Convert `mono` signal to `stereo` using the static `pan` ratio.
    
    pan = -1.0 is hard left. pan = 0.0 is center. pan = 1.0 is hard right.
    """
    cdef int32_t i
    for i in range(want_frames):
            stereo.data.as_shorts[2 * i] = <int16_t>((-pan + 1) / 2 * mono.data.as_shorts[i])
            stereo.data.as_shorts[2 * i + 1] = <int16_t>((pan + 1) / 2 * mono.data.as_shorts[i])


cdef class Envelope:
    """A typical linear Attack-Decay-Sustain-Release envelope.
    
    Output in range 0.0 - 1.0.
    """

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

    cpdef advance(self):
        """Move the envelope one sample forward and return its current floating-point value."""
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

    cpdef is_silent(self):
        return self.samples_since_reset < 0 and self.current_value == 0


cdef class Operator:
    """A Yamaha-style FM operator which is a waveform coupled with an envelope.

    Generates monophonic audio with `mono_out` which can be modulated with
    a `modulator` array input, possibly from another Operator.
    """

    # See field hints in `__init__` below.
    cdef array.array wave
    cdef int sample_rate
    cdef Envelope envelope
    cdef double volume
    cdef double pitch

    # Current state of the operator, modified during `mono_out()`
    cdef double current_velocity
    cdef bint reset

    def __init__(
        self,
        array.array wave,  # "h" arrays assumed, which are signed 16-bit
        int sample_rate,  # Hz, like: 44100
        Envelope envelope,
        double volume = 1.0,  # 0.0 - 1.0; relative attenuation
        double pitch = 440.0,  # Hz
    ):
        self.wave = wave
        self.sample_rate = sample_rate
        self.envelope = envelope
        self.volume = volume
        self.pitch = pitch
        self.current_velocity = 0.0
        self.reset = False

    def note_on(self, double pitch, double volume):
        self.reset = True
        self.pitch = pitch
        self.current_velocity = volume

    def note_off(self, double pitch, double volume):
        self.envelope.release()

    def mono_out(self):
        """Generate Audio, accepting other Audio for modulation purposes.
        
        Audio is generated with sample-precision pitch changes, and sample-precision
        resettable envelope.

        By design, the waveform is not reset until the sound is silent (passes through
        the entire envelope).
        """
        cdef array.array modulator
        cdef int mod_len
        cdef array.array out_buffer = array.array("h")
        cdef double w_i = 0.0

        modulator = yield out_buffer
        mod_len = len(modulator)
        out_buffer.extend([0] * MAX_BUFFER)
        while True:
            w_i = self.modulate(out_buffer, modulator, w_i)
            if self.reset:
                self.reset = False
                self.envelope.reset()
            modulator = yield out_buffer[:mod_len]
            mod_len = len(modulator)

    @cython.cdivision(True)
    cpdef modulate(self, array.array out_buffer, array.array modulator, double w_i):
        """Fill `out_buffer` with an enveloped and attenuated chunk of `self.wave`.

        The waveform is modulated by a `modulator` waveform which can be an output
        of another Operator. By design velocity, volume, pitch, and the envelope
        can change with sample-precision.

        If you don't want modulation, use an identity `modulator` array (1-filled).
        """
        cdef int i
        cdef int16_t mod
        cdef double mod_scaled
        cdef double triangle_factor
        cdef int sr = self.sample_rate
        cdef int16_t[:] w = self.wave
        cdef int w_len = len(w)
        envelope = self.envelope

        if envelope.is_silent():
            for i in range(len(modulator)):
                out_buffer[i] = 0
            return 0.0

        for i in range(len(modulator)):
            mod = modulator.data.as_shorts[i]
            mod_scaled = w_i + mod * w_len / INT16_MAXVALUE
            triangle_factor = mod_scaled - floor(mod_scaled)
            out_buffer.data.as_shorts[i] = saturate(
                self.current_velocity
                * self.volume
                * envelope.advance()
                * (
                    (1.0 - triangle_factor) * w[<int>mod_scaled % w_len]
                    + triangle_factor * w[<int>(mod_scaled + 1.0) % w_len]
                )
            )
            w_i += w_len * <double>self.pitch / sr
        return w_i

    def is_silent(self):
        return not self.reset and self.envelope.is_silent()