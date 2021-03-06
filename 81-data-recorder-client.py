import os
import shutil

import numpy as np
import paramiko
import zmq
from tqdm import tqdm
import time

from config.constants import *
from exchange.utilities import zmq_recv_array
from movements.dataset import Dataset
from recorder.shittykinect import ShittyKinect
from recorder.utilities import progress_write, progress_read

DATASET_PATH_CLEAN = "data/recording1_clean.npz"
PROGRESS_FILE = "data/recording_progress"
# EPISODE = 0

# MOTOR 0 & 3 ARE INVERTED

ds = Dataset()
ds.load(DATASET_PATH_CLEAN)

port = "5556"
context = zmq.Context()
socket = context.socket(zmq.PAIR)
socket.connect("tcp://flogo3.local:%s" % port)
# welcome = socket.recv_string()
# print (welcome)
poller = zmq.Poller()
poller.register(socket, zmq.POLLIN)

kinect = ShittyKinect()
frame = kinect.getFrame()  # warmup
print ("got first frame")
print (frame.shape)

ds_shape = ds.moves.shape


def move_robot(action):
    out = []
    for row in range(len(action)):
        out.append(",".join([str(s) for s in action[row].tolist()]))
    output = "|".join(out)
    socket.send_string(output)


def save_stuff(data_buf_kinect, data_buf_kinect_time, data_buf_robo, data_buf_robo_time, data_buf_robo_speed, episode_idx, save_episode):
    data_kinect = np.array(data_buf_kinect)
    data_kinect_time = np.array(data_buf_kinect_time)
    data_robo = np.array(data_buf_robo)
    data_robo_time = np.array(data_buf_robo_time, dtype=np.uint64)
    data_robo_speed = np.array(data_buf_robo_speed)

    np.savez_compressed("data/data_dump_tmp.npz",
                        kinect=data_kinect,
                        kinect_time=data_kinect_time,
                        robo=data_robo,
                        robo_time=data_robo_time,
                        robo_speed=data_robo_speed
                        )

    output_filename = "data_dump_{}.npz".format(save_episode)
    output_path = "data/" + output_filename

    shutil.move("data/data_dump_tmp.npz", output_path)

    if (USE_BACKUP):
        ssh = paramiko.SSHClient()
        ssh.load_host_keys(os.path.expanduser(os.path.join("~", ".ssh", "known_hosts")))
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        ssh.connect(BACKUP_HOST, username=BACKUP_USER, password=BACKUP_PASS)
        sftp = ssh.open_sftp()

        sftp.put(output_path, BACKUP_PATH + output_filename)

        sftp.close()
        ssh.close()
        print ("SSH: file transfered")
        os.remove(output_path)

    progress_write(PROGRESS_FILE, episode_idx)

    print ("SAVED. Episode {}".format(episode_idx))


progress = progress_read(PROGRESS_FILE)
print ("LOADED PROGRESS:", progress)

data_buffer_kinect = []
data_buffer_kinect_time = []
data_buffer_robo = []
data_buffer_robo_time = []
data_buffer_robo_speed = []

save_episode_count = int(progress / WRITE_EVERY_N_EPISODES)

for episode_idx in tqdm(range(len(ds.moves))):
    if episode_idx < progress:
        continue

    actions = np.around(ds.moves[episode_idx, :, 0, :], 2)
    frames = []
    frames_time = []
    move_robot(actions)
    while True:
        frame = kinect.getFrame()
        frames.append(frame)
        frames_time.append(time.time()*TIME_MULTI)
        socks = dict(poller.poll(1000 * ROBO_FPD_DELAY))
        if socks:
            if socks.get(socket) == zmq.POLLIN:
                robo_frames = zmq_recv_array(socket)
                robo_frames_time = zmq_recv_array(socket)
                robo_frames_speed = zmq_recv_array(socket)

                frames = np.array(frames)
                frames_time = np.array(frames_time, dtype=np.uint64)

                data_buffer_kinect.append(frames)
                data_buffer_kinect_time.append(frames_time)
                data_buffer_robo.append(robo_frames)
                data_buffer_robo_time.append(robo_frames_time)
                data_buffer_robo_speed.append(robo_frames_speed)
                break
    if len(data_buffer_kinect) == WRITE_EVERY_N_EPISODES:
        save_stuff(data_buffer_kinect,
                   data_buffer_kinect_time,
                   data_buffer_robo,
                   data_buffer_robo_time,
                   data_buffer_robo_speed,
                   episode_idx,
                   save_episode_count)
        save_episode_count += 1
        data_buffer_kinect = []
        data_buffer_kinect_time = []
        data_buffer_robo = []
        data_buffer_robo_time = []
        data_buffer_robo_speed = []

kinect.close()
