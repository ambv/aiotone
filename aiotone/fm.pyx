DEF INT16_MAXVALUE = 32767


cpdef saturate(double value):
    return max(min(int(value), INT16_MAXVALUE), -INT16_MAXVALUE)
