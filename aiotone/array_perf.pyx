import cython

from cpython cimport array

@cython.boundscheck(False)
@cython.wraparound(False)
def update_buffer(array.array arr, bytearray data):
    cdef unsigned char *raw_arr = arr.data.as_uchars
    for i in range(len(data)):
        raw_arr[i] = data[i]
