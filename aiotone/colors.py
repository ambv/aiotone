#!/usr/bin/env python3.11

from __future__ import annotations

from colorsys import rgb_to_hsv, hsv_to_rgb


# types
Hf = float
Sf = float
Vf = float
HSVf = tuple[Hf, Sf, Vf]
Ri = int  # 0..255
Gi = int  # 0..255
Bi = int  # 0..255
RGBi = tuple[Ri, Gi, Bi]


def convert_html_to_hsv(colors: str) -> list[HSVf]:
    colors = colors.replace("#", "")
    result = []
    for color in colors.split(","):
        color = color.strip()
        if len(color) != 6:
            raise ValueError("Invalid color: {}".format(color))
        r = int(color[0:2], 16) / 255
        g = int(color[2:4], 16) / 255
        b = int(color[4:6], 16) / 255
        result.append(rgb_to_hsv(r, g, b))

    # fix black and white so that the transitions aren't too jarring
    black = result[0]
    first_color = result[1]
    if abs(black[0] - first_color[0] + black[1] - first_color[1]) > 1:
        result[0] = first_color[0], first_color[1], black[2]
    white = result[-1]
    last_color = result[-2]
    if abs(white[0] - last_color[0] + white[1] - last_color[1]) > 1:
        result[-1] = last_color[0], white[1], white[2]

    return result


def get_color(value: float, brightness: float, color_buckets) -> RGBi:
    value *= brightness  # assuming signal is between 0.0 and 1.0

    last_bucket = None
    last_color = None
    transition = 0
    for curr_bucket, curr_color in color_buckets:
        if curr_bucket >= value:
            if last_bucket is not None:
                transition = (value - last_bucket) / (curr_bucket - last_bucket)
            else:
                last_color = curr_color
            break

        last_bucket = curr_bucket
        last_color = curr_color

    h = last_color[0] + transition * (curr_color[0] - last_color[0])
    s = last_color[1] + transition * (curr_color[1] - last_color[1])
    v = last_color[2] + transition * (curr_color[2] - last_color[2])
    result = hsv_to_rgb(h, s, v)
    return (
        int(round(255 * result[0])),
        int(round(255 * result[1])),
        int(round(255 * result[2])),
    )


def colors_to_buckets(
    colors: list[HSVf], min: int = 0, max: int = 1
) -> tuple[tuple[float, HSVf], ...]:
    step = (max - min) / (len(colors) - 1)
    result = []
    for i in range(len(colors)):
        result.append((min + i * step, colors[i]))
    return tuple(result)
