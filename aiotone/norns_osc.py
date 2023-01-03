import random
import time

from pythonosc import udp_client

norns = udp_client.SimpleUDPClient("192.168.1.246", 10111)


octaves = [36, 48, 48, 60, 60, 60, 72, 72, 84]

while True:
    time.sleep(random.random())
    norns.send_message("/param/delay_rate", random.random()/50+1.98)
    if random.random() > 0.5:
        norns.send_message("/param/delay_pan", random.random()*2-1.0)
    if random.random() > 0.8:
        norns.send_message("/param/root_note", random.choice(octaves))