import cython

from cpython cimport array

@cython.boundscheck(False)
@cython.wraparound(False)
@cython.nonecheck(False)
def update_buffer(array.array arr, bytearray data):
    cdef unsigned char *raw_arr = arr.data.as_uchars
    for i in range(len(data)):
        raw_arr[i] = data[i]


@cython.boundscheck(False)
@cython.wraparound(False)
@cython.nonecheck(False)
def move_audio(
    array.array in_buffer,
    int in_l,
    int in_r,
    array.array out_buffer,
    int out_l,
    int out_r,
    array.array channel_sum
) -> None:
    cdef int offset
    cdef int ch
    cdef int ch_len = len(channel_sum)
    cdef int out_len = len(out_buffer)
    cdef float *in_buffer_raw = in_buffer.data.as_floats
    cdef float *out_buffer_raw = out_buffer.data.as_floats
    cdef float *channel_sum_raw = channel_sum.data.as_floats

    for offset from 0 <= offset < out_len by ch_len:
        for ch in range(ch_len):
            channel_sum_raw[ch] += abs(in_buffer_raw[offset + ch])

            if ch == out_l:
                out_buffer_raw[offset + ch] = in_buffer_raw[offset + in_l]
            elif ch == out_r:
                out_buffer_raw[offset + ch] = in_buffer_raw[offset + in_r]
            else:
                out_buffer_raw[offset + ch] = 0.0