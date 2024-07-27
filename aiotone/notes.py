from typing import Literal, cast

# fmt: off
Note = Literal[12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125, 126, 127]
Notes = list[Note]
# fmt: on

C: Notes = [12]
Cs: Notes = [13]
D: Notes = [14]
Ds: Notes = [15]
E: Notes = [16]
F: Notes = [17]
Fs: Notes = [18]
G: Notes = [19]
Gs: Notes = [20]
A: Notes = [21]
As: Notes = [22]
B: Notes = [23]

Db = Cs
Eb = Ds
Gb = Fs
Ab = Gs
Bb = As

all_notes = (C, Cs, D, Ds, E, F, Fs, G, Gs, A, As, B)

for note in all_notes:
    for octave in range(1, 9):
        note.append(cast(Note, note[0] + 12 * octave))

notes_to_name: dict[int, str] = {
    id(C): "C",
    id(Cs): "C#",
    id(D): "D",
    id(Ds): "D#",
    id(E): "E",
    id(F): "F",
    id(Fs): "F#",
    id(G): "G",
    id(Gs): "G#",
    id(A): "A",
    id(As): "A#",
    id(B): "B",
}
note_to_name: dict[Note, str] = {}
for notes in all_notes:
    for i, n in enumerate(notes):
        note_to_name[n] = f"{notes_to_name[id(notes)]}{i}"

note_to_freq: dict[Note, float] = {}
for note in all_notes:
    for n in note:
        note_to_freq[n] = 440 * 2 ** ((n - 69) / 12)
