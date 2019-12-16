C = [24]
Cs = [25]
D = [26]
Ds = [27]
E = [28]
F = [29]
Fs = [30]
G = [31]
Gs = [32]
A = [33]
As = [34]
B = [35]

Db = Cs
Eb = Ds
Gb = Fs
Ab = Gs
Bb = As

for note in (C, Cs, D, Ds, E, F, Fs, G, Gs, A, As, B):
    for octave in range(1, 5):
        note.append(note[0] + 12 * octave)
