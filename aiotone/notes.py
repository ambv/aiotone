C = [12]
Cs = [13]
D = [14]
Ds = [15]
E = [16]
F = [17]
Fs = [18]
G = [19]
Gs = [20]
A = [21]
As = [22]
B = [23]

Db = Cs
Eb = Ds
Gb = Fs
Ab = Gs
Bb = As

all_notes = (C, Cs, D, Ds, E, F, Fs, G, Gs, A, As, B)

for note in all_notes:
    for octave in range(1, 9):
        note.append(note[0] + 12 * octave)

note_to_freq = {}
for note in all_notes:
    for n in note:
        note_to_freq[n] = 440 * 2 ** ((n - 69) / 12)
