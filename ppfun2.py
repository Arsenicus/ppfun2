#!/usr/bin/env python3

# PixelPlanet bot version 2 by portasynthinca3 (now using WebSockets!)
# Distributed under WTFPL

not_inst_libs = []

import threading
import requests, json
import time, datetime, math, random
import os.path as path, getpass

try:
    from playsound import playsound
except ImportError:
    not_inst_libs.append('playsound')

try:
    import numpy as np
except ImportError:
    not_inst_libs.append('numpy')

try:
    import cv2
except ImportError:
    not_inst_libs.append('opencv-python')

try:
    import websocket
except ImportError:
    not_inst_libs.append('websocket_client')

try:
    from colorama import Fore, Back, Style, init
except ImportError:
    not_inst_libs.append('colorama')

# tell the user to install libraries
if len(not_inst_libs) > 0:
    print('Some libraries are not installed. Install them by running this command:\npip install ' + ' '.join(not_inst_libs))
    exit()

me = {}

# the version of the bot
VERSION     = '1.1.4'
VERSION_NUM = 5

# the URLs of the current version and c.v. definitions
BOT_URL    = 'https://raw.githubusercontent.com/portasynthinca3/ppfun2/master/ppfun2.py'
VERDEF_URL = 'https://raw.githubusercontent.com/portasynthinca3/ppfun2/master/verdef'

# are we allowed to draw
draw = True
# was the last placing of the pixel successful
succ = False

# chunk data cache
chunk_data = None

# number of pixels drawn and the starting time
pixels_drawn = 1
start_time = None

# play a notification sound
def play_notification():
    playsound('notif.mp3')

# shows the image in a window
def show_image(img):
    print(f'{Fore.YELLOW}Scroll to zoom, drag to pan, press any key to close the window{Style.RESET_ALL}')
    cv2.imshow('image', img)
    cv2.waitKey(0)
    cv2.destroyAllWindows()

# gets raw chunk data from the server
def get_chunk(d, x, y):
    # get data from the server
    data = requests.get(f'https://pixelplanet.fun/chunks/{d}/{x}/{y}.bmp').content
    # construct a numpy array from it
    arr = np.zeros((256, 256), np.uint8)
    for i in range(65536):
        c = data[i]
        # protected pixels are shifted up by 128
        if c >= 128:
            c = c - 128
        arr[i // 256, i % 256] = c
    return arr

# gets several map chunks from the server
def get_chunks(d, xs, ys, w, h):
    # the final image
    data = np.zeros((0, w * 256), np.uint8)
    # go through the chunks
    for y in range(h):
        # the row
        row = np.zeros((256, 0), np.uint8)
        for x in range(w):
            # append the chunk to the row
            row = np.concatenate((row, get_chunk(d, x + xs, y + ys)), axis=1)
        # append the row to the image
        data = np.concatenate((data, row), axis=0)
    return data

# renders chunk as colored CV2 image
def render_chunk(d, x, y):
    global me
    data = get_chunk(d, x, y)
    img = np.zeros((256, 256, 3), np.uint8)
    colors = me['canvases'][str(d)]['colors']
    # go through the data
    for y in range(256):
        for x in range(256):
            r, g, b = colors[data[y, x]]
            img[y, x] = (b, g, r)
    return img

# renders map data into a colored CV2 image
def render_map(d, data):
    global me
    img = np.zeros((data.shape[0], data.shape[1], 3), np.uint8)
    colors = me['canvases'][str(d)]['colors']
    # go through the data
    for y in range(data.shape[0]):
        for x in range(data.shape[1]):
            r, g, b = colors[data[y, x]]
            img[y, x] = (b, g, r)
    return img

# selects a canvas for future use
def select_canvas(ws, d):
    # construct the data array
    data = bytearray(2)
    data[0] = 0xA0
    data[1] = d
    # send data
    ws.send_binary(data)

# register a chunk
def register_chunk(ws, d, x, y):
    # construct the data array
    data = bytearray(3)
    data[0] = 0xA1
    data[1] = x
    data[2] = y
    # send data
    ws.send_binary(data)

# places a pixel
def place_pixel(ws, d, x, y, c):
    # convert the X and Y coordinates to I, J and Offset
    csz = me['canvases'][str(d)]['size']
    modOffs = (csz // 2) % 256
    offs = (((y + modOffs) % 256) * 256) + ((x + modOffs) % 256)
    i = (x + csz // 2) // 256
    j = (y + csz // 2) // 256
    # construct the data array
    data = bytearray(7)
    data[0] = 0xC1
    data[1] = i
    data[2] = j
    data[3] = (offs >> 16) & 0xFF
    data[4] = (offs >>  8) & 0xFF
    data[5] = (offs >>  0) & 0xFF
    data[6] = c
    # send data
    ws.send_binary(data)

# draws the image
def draw_function(ws, canv_id, draw_x, draw_y, c_start_x, c_start_y, img, defend, strategy):
    global me, draw, succ, chunk_data, pixels_drawn, start_time

    size = img.shape
    canv_sz = me['canvases'][str(canv_id)]['size']
    canv_clr = me['canvases'][str(canv_id)]['colors']

    # fill a list of coordinates based on the strategy
    coords = []
    if strategy == 'forward':
        for y in range(size[0]):
            for x in range(size[1]):
                coords.append((x, y))
    elif strategy == 'backward':
        for y in range(size[0] - 1, -1, -1):
            for x in range(size[1] - 1, -1, -1):
                coords.append((x, y))
    elif strategy == 'random':
        for y in range(size[0]):
            for x in range(size[1]):
                coords.append((x, y))
        random.shuffle(coords)

    # calculate position in the chunk data array
    start_in_d_x = draw_x + ((canv_sz // 2) - (c_start_x * 256))
    start_in_d_y = draw_y + ((canv_sz // 2) - (c_start_y * 256))

    start_time = datetime.datetime.now()
    draw = True

    while len(coords) > 0:
        # get a coordinate
        coord = coords[0]
        x, y = (coord)
        coords.remove(coord)

        # check if the pixel is transparent
        if img[y, x] == 255:
            continue

        succ = False
        while not succ:
            # we need to compare actual color values and not indicies
            # because water and land have seprate indicies, but the same color values
            #  as regular colors
            if canv_clr[chunk_data[start_in_d_y + y, start_in_d_x + x]] != canv_clr[img[y, x]]:
                pixels_remaining = len(coords)
                sec_per_px = (datetime.datetime.now() - start_time).total_seconds() / pixels_drawn
                time_remaining = datetime.timedelta(seconds=(pixels_remaining * sec_per_px))
                print(f'{Fore.YELLOW}Placing a pixel at {Fore.GREEN}({x + draw_x}, {y + draw_y})' + 
                    f'{Fore.YELLOW}, progress: {Fore.GREEN}{"{:2.4f}".format((y * size[0] + x) * 100 / (size[0] * size[1]))}%' +
                    f'{Fore.YELLOW}, remaining: {Fore.GREEN}{"estimating" if pixels_drawn < 20 else str(time_remaining)}' +
                    f'{Fore.YELLOW}, {Fore.GREEN}{pixels_drawn}{Fore.YELLOW} pixels placed{Style.RESET_ALL}')
                # get the color index
                c_idx = img[y, x]
                # try to draw it
                while not draw:
                    time.sleep(0.25)
                    pass
                draw = False
                place_pixel(ws, canv_id, x + draw_x, y + draw_y, c_idx)
                # this flag will be reset when the other thread receives a confirmation message
                while not draw:
                    time.sleep(0.25)
                    pass
                if succ:
                    pixels_drawn += 1
                # wait half a second
                # (a little bit of artifical fluctuation
                #  so the server doesn't think we're a bot)
                time.sleep(0.5 + random.uniform(-0.25, 0.25))
            else:
                succ = True

    print(f'{Fore.GREEN}Done drawing{Style.RESET_ALL}')
    if not defend:
        return
    print(f'{Fore.GREEN}Entering defend mode{Style.RESET_ALL}')

    # do the same thing, but now in a loop that checks everything once per second
    while True:
        for y in range(size[0]):
            for x in range(size[1]):
                if img[y, x] == 255:
                    continue
                if canv_clr[chunk_data[start_in_d_y + y, start_in_d_x + x]] != canv_clr[img[y, x]]:
                    print(f'{Fore.YELLOW}[DEFENDING] Placing a pixel at {Fore.GREEN}({x + draw_x}, {y + draw_y}){Style.RESET_ALL}')
                    # get the color index
                    c_idx = img[y, x]
                    # try to draw it
                    while not draw:
                        time.sleep(0.25)
                        pass
                    draw = False
                    place_pixel(ws, canv_id, x + draw_x, y + draw_y, c_idx)
                    # this flag will be reset when the other thread receives a confirmation message
                    while not draw:
                        time.sleep(0.25)
                        pass
                    # wait half a second
                    # (a little bit of artifical fluctuation
                    #  so the server doesn't think we're a bot)
                    time.sleep(0.5 + random.uniform(-0.25, 0.25))
        time.sleep(1)

def main():
    global me, draw, succ, chunk_data
    # initialize colorama
    init()

    # get the version on the server
    print(f'{Fore.YELLOW}PixelPlanet bot by portasynthinca3 version {Fore.GREEN}{VERSION}{Fore.YELLOW}\nChecking for updates{Style.RESET_ALL}')
    server_verdef = requests.get(VERDEF_URL).text
    if int(server_verdef.split('\n')[1]) > VERSION_NUM:
        # update
        server_ver = server_verdef.split('\n')[0]
        print(f'{Fore.YELLOW}There\'s a new version {Fore.GREEN}{server_ver}{Fore.YELLOW} on the server. Downloading{Style.RESET_ALL}')
        with open('ppfun2.py', 'wb') as bot_file:
            bot_file.write(requests.get(BOT_URL).content)
        print(f'{Fore.YELLOW}Please start the bot again{Style.RESET_ALL}')
        exit()
    else:
        print(f'{Fore.YELLOW}You\'re running the latest version{Style.RESET_ALL}')

    # get canvas info list and user identifier
    print(f'{Fore.YELLOW}Requesting initial data{Style.RESET_ALL}')
    me = requests.get('https://pixelplanet.fun/api/me').json()

    # authorize
    print(f'{Fore.YELLOW}Enter your PixelPlanet username or e-mail (leave empty to skip authorization): {Style.RESET_ALL}', end='')
    login = input()
    passwd = ''
    auth_token = ''
    extra_ws_headers = []
    if login != '':
        passwd = getpass.getpass(f'{Fore.YELLOW}Enter your PixelPlanet password: {Style.RESET_ALL}')
        print(f'{Fore.YELLOW}Authorizing{Style.RESET_ALL}')
        response = requests.post('https://pixelplanet.fun/api/auth/local', json={'nameoremail':login, 'password':passwd})
        resp_js = response.json()
        if 'success' in resp_js and resp_js['success']:
            print(f'{Fore.YELLOW}Logged in as {Fore.GREEN}{resp_js["me"]["name"]}{Style.RESET_ALL}')
            # get the token and add it as a WebSocket cookie
            auth_token = response.cookies.get('pixelplanet.session')
            extra_ws_headers.append("Cookie: pixelplanet.session=" + auth_token)
        else:
            print(f'{Fore.RED}Authorization failed{Style.RESET_ALL}')

    # ask for proxy
    print(f'{Fore.YELLOW}Enter your proxy (host:port), leave empty to not use a proxy: {Style.RESET_ALL}', end='')
    proxy_host = input()
    proxy_port = None
    if proxy_host != '':
        proxy_port = int(proxy_host.split(':')[1])
        proxy_host = proxy_host.split(':')[0]
    else:
        proxy_host = None

    # request some info from the user
    print(f'{Fore.YELLOW}Enter a path to the image:{Style.RESET_ALL} ', end='')
    img_path = input()

    print(f'{Fore.YELLOW}Enter the X coordiante of the top-left corner:{Style.RESET_ALL} ', end='')
    draw_x = int(input())
    print(f'{Fore.YELLOW}Enter the Y coordiante of the top-left corner:{Style.RESET_ALL} ', end='')
    draw_y = int(input())

    # defend the image?
    defend = ''
    while defend not in ['y', 'n', 'yes', 'no']:
        print(f'{Fore.YELLOW}Defend [y, n]?{Style.RESET_ALL} ', end='')
        defend = input().lower()
    defend = True if defend in ['y', 'yes'] else False

    # choose a strategy
    strategies = ['forward', 'backward', 'random']
    strategy = None
    while strategy not in strategies:
        print(f'{Fore.YELLOW}Choose the drawing strategy [forward/backward/random]:{Style.RESET_ALL} ', end='')
        strategy = input().lower()
    
    # choose the canvas
    canv_id = -1
    while str(canv_id) not in me['canvases']:
        print(Fore.YELLOW + '\n'.join(['[' + (Fore.GREEN if ("v" not in me["canvases"][k]) else Fore.RED) + f'{k}{Fore.YELLOW}] ' +
                                           me['canvases'][k]['title'] for k in me['canvases']]))
        print(f'Select the canvas [0-{len(me["canvases"]) - 1}]:{Style.RESET_ALL} ', end='')
        canv_id = input()
        if 0 <= int(canv_id) <= len(me['canvases']) - 1:
            if 'v' in me['canvases'][canv_id]:
                print(Fore.RED + 'This canvas is not supported, only 2D canvases are supported' + Style.RESET_ALL)
                canv_id = -1

    canv_desc = me['canvases'][canv_id]
    canv_id = int(canv_id)

    # load the image
    print(f'{Fore.YELLOW}Loading the image{Style.RESET_ALL}')
    img = None
    img_size = (0, 0)
    try:
        img = cv2.imread(img_path, cv2.IMREAD_UNCHANGED)
        img_size = img.shape[:2]
    except:
        print(f'{Fore.RED}Failed to load the image. Does it exist? Is it an obscure image format?{Style.RESET_ALL}')
        exit()
    # check if it's JPEG
    img_extension = path.splitext(img_path)[1]
    if img_extension in ['jpeg', 'jpg']:
        print(f'{Fore.RED}WARNING: you appear to have loaded a JPEG image. It uses lossy compression, so it\'s not good at all for pixel-art.{Style.RESET_ALL}')
    
    # transform the colors
    print(f'{Fore.YELLOW}Processing the image{Style.RESET_ALL}')
    color_idxs = np.zeros(img_size, np.uint8)
    preview = np.zeros((img_size[0], img_size[1], 4), np.uint8)
    for y in range(img_size[0]):
        for x in range(img_size[1]):
            # ignore the pixel if it's transparent
            transparent = None
            if img.shape[2] == 3: # the image doesn't have an alpha channel
                transparent = False
            else: # the image has an alpha channel
                if img[y, x][3] > 128:
                    transparent = False
                else:
                    transparent = True
            if not transparent:
                # fetch BGR color
                bgr = img[y, x]
                bgr = (int(bgr[0]), int(bgr[1]), int(bgr[2]))
                # find the nearest one in the palette
                best_diff = 1000000000
                best_no = 0
                # ignore the first two colors, they show the land a water colors and are not allowed in the request
                for i in range(2, len(canv_desc['colors'])):
                    c_bgr = tuple(canv_desc['colors'][i])
                    diff = (c_bgr[2] - bgr[0]) ** 2 + (c_bgr[1] - bgr[1]) ** 2 + (c_bgr[0] - bgr[2]) ** 2
                    if diff < best_diff:
                        best_diff = diff
                        best_no = i
                # store the color idx
                color_idxs[y, x] = best_no
                # store the color for preview
                preview[y, x] = tuple(canv_desc['colors'][best_no] + [255])
                # PixelPlanet uses RGB, OpenCV uses BGR, need to swap
                temp = preview[y, x][2]
                preview[y, x][2] = preview[y, x][0]
                preview[y, x][0] = temp
            else:
                # checkerboard pattern in transparent parts of the image
                brightness = 0
                if y % 10 >= 5:
                    brightness = 128 if x % 10 >= 5 else  64
                else:
                    brightness = 64  if x % 10 >= 5 else 128
                preview[y, x] = (brightness, brightness, brightness, 255)
                color_idxs[y, x] = 255

    # show the preview
    show_preview = ''
    while show_preview not in ['y', 'n', 'yes', 'no']:
        print(f'{Fore.YELLOW}Show the preview [y/n]?{Style.RESET_ALL} ', end='')
        show_preview = input().lower()
    if show_preview in ['y', 'yes']:
        show_image(preview)

    # load the chunks in the region of the image
    print(f'{Fore.YELLOW}Loading chunk data around the destination{Style.RESET_ALL}')
    csz = me['canvases'][str(canv_id)]['size']
    c_start_y = ((csz // 2) + draw_y) // 256
    c_start_x = ((csz // 2) + draw_x) // 256
    c_end_y = ((csz // 2) + draw_y + img.shape[0]) // 256
    c_end_x = ((csz // 2) + draw_x + img.shape[1]) // 256
    c_occupied_y = c_end_y - c_start_y + 1
    c_occupied_x = c_end_x - c_start_x + 1
    chunk_data = get_chunks(canv_id, c_start_x, c_start_y, c_occupied_x, c_occupied_y)
    # show them
    show_chunks = ''
    while show_chunks not in ['y', 'n', 'yes', 'no']:
        print(f'{Fore.YELLOW}Show the area around the destination [y/n]?{Style.RESET_ALL} ', end='')
        show_chunks = input().lower()
    if show_chunks in ['y', 'yes']:
        print(f'{Fore.YELLOW}Processing...{Style.RESET_ALL}')
        show_image(render_map(canv_id, chunk_data))

    start = ''
    while start not in ['y', 'n', 'yes', 'no']:
        print(f'{Fore.YELLOW}Draw {Fore.GREEN}{img_path}{Fore.YELLOW} ' +
              f'at {Fore.GREEN}({draw_x}, {draw_y}){Fore.YELLOW} ' + 
              f'on canvas {Fore.GREEN}{me["canvases"][str(canv_id)]["title"]} {Fore.YELLOW}[y/n]?{Style.RESET_ALL} ', end='')
        start = input().lower()
    # abort if user decided not to draw
    if start not in ['y', 'yes']:
        exit()

    # start a WebSockets connection
    print(f'{Fore.YELLOW}Connecting to the server{Style.RESET_ALL}')
    ws = websocket.create_connection('wss://pixelplanet.fun:443/ws', header=extra_ws_headers, http_proxy_host=proxy_host, http_proxy_port=proxy_port)
    select_canvas(ws, canv_id)
    # register the chunks
    for c_y in range(c_occupied_y):
        for c_x in range(c_occupied_x):
            register_chunk(ws, canv_id, c_x + c_start_x, c_y + c_start_y)
    # start drawing
    thr = threading.Thread(target=draw_function, args=(ws, canv_id, draw_x, draw_y, c_start_x, c_start_y, color_idxs, defend, strategy), name='Drawing thread')
    thr.start()
    # read server messages
    while True:
        data = ws.recv()
        # text data = chat message
        if type(data) == str:
            # data comes as a JS array
            msg = json.loads('{"msg":' + data + '}')
            msg = msg['msg']
            print(f'{Fore.GREEN}{msg[0]}{Fore.YELLOW} (country: {Fore.GREEN}{msg[2]}{Fore.YELLOW}) ' + 
                    f'says: {Fore.GREEN}{msg[1]}{Fore.YELLOW} in chat {Fore.GREEN}{"int" if msg[2] == 0 else "en"}{Style.RESET_ALL}')
        # binary data = event
        else:
            opcode = data[0]
            # online counter
            if opcode == 0xA7:
                oc = (data[1] << 8) | data[2]
                print(f'{Fore.YELLOW}Online counter: {Fore.GREEN}{oc}{Style.RESET_ALL}')

            # total cooldown packet
            elif opcode == 0xC2:
                cd = (data[4] << 24) | (data[3] << 16) | (data[2] << 8) | data[1]
                print(f'{Fore.YELLOW}Total cooldown: {Fore.GREEN}{cd} ms{Style.RESET_ALL}')

            # pixel return packet
            elif opcode == 0xC3:
                rc = data[1]
                wait = (data[2] << 24) | (data[3] << 16) | (data[4] << 8) | data[5]
                cd_s = (data[6] << 8) | data[7]
                print(f'{Fore.YELLOW}Pixel return{Fore.YELLOW} (code: {Fore.RED if rc != 0 else Fore.GREEN}{rc}{Fore.YELLOW}): ' + 
                        f'wait: {Fore.GREEN}{wait}{Fore.YELLOW} ms {Fore.GREEN}[+{cd_s} s]{Style.RESET_ALL}')
                # CAPTCHA error
                if rc == 10:
                    draw = False
                    play_notification()
                    print(Fore.RED + 'Place a pixel somewhere manually and enter CAPTCHA' + Style.RESET_ALL)
                # any error
                if rc != 0:
                    time.sleep(2)
                    succ = False
                    draw = True
                # placement was successful
                else:
                    if wait >= 30000:
                        print(f'{Fore.YELLOW}Cooling down{Style.RESET_ALL}')
                        # wait that many seconds plus 1 (to be sure)
                        time.sleep(cd_s + 1)
                    succ = True
                    draw = True

            # pixel update
            elif opcode == 0xC1:
                # get raw data
                i = data[1]
                j = data[2]
                offs = (data[3] << 16) | (data[4] << 8) | data[5]
                clr = data[6]
                # convert it to X and Y coords
                csz = me['canvases'][str(canv_id)]['size']
                x = ((i * 256) - (csz // 2)) + (offs & 0xFF)
                y = ((j * 256) - (csz // 2)) + ((offs >> 8) & 0xFF)
                print(f'{Fore.YELLOW}Pixel update at {Fore.GREEN}({str(x)}, {str(y)}){Style.RESET_ALL}')
                # write that change
                local_x = (i - c_start_x) * 256 + (offs & 0xFF)
                local_y = (j - c_start_y) * 256 + ((offs >> 8) & 0xFF)
                chunk_data[local_y, local_x] = clr
            else:
                print(f'{Fore.RED}Unreconized data opcode from the server. Raw data: {data}{Style.RESET_ALL}')

if __name__ == "__main__":
    main()