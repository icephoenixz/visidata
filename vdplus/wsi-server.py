#!/usr/bin/env python3

import sys
import json
import http.server
import urllib.parse
import uuid
import random


player_colors = 'green yellow cyan magenta red blue'.split()
planet_names = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
map_width = 10
map_height = 10
rand = random.randrange

class Game:
    def __init__(self):
        self.game_started = False
        self.players = {}   # [playername] -> Player
        self.planets = {}   # [planetname] -> Planet

    def start_game(self):
        self.generate_planets()

    @property
    def started(self):
        return bool(self.planets)

    def POST_join(self, pl, **kwargs):
        if self.started:
            raise HTTPException(402, 'Game already started')

        if pl.number is not None:
            return 'already player %s/%s' % (pl.number, len(self.players))

        pl.number = len(self.players)
        self.players[pl.name] = pl
        return pl.sessionid  # leaky

    def GET_ready(self, pl, **kwargs):
        if self.started:
            raise HTTPException(402, 'Game already started')

        if not pl:
            raise HTTPException(403, 'Unauthorized')

        pl.ready = True
        if len(self.players) > 1 and all(pl.ready for pl in self.players.values()):
            self.start_game()
            return 'game started'

        return 'player ready'

    def GET_players(self, pl, **kwargs):
        return [x.as_tuple() for x in self.players.values()]

    def GET_planets(self, pl, **kwargs):
        return [x.as_tuple() for x in self.planets.values()]

    def GET_deployments(self, pl, **kwargs):
        return [x.as_tuple() for x in self.planets.values()]

    def POST_deploy(self, launch_player, launch_planet=None, dest_planet=None, dest_turn=None, nships=0):
        launch_planet = self.planets.get(launch_planet) or error('no such planet %s' % launch_planet)
        if launch_player is not launch_planet.owner:
            error('player does not own planet')

        dest_planet = self.planets.get(dest_planet) or error('no such planet %s' % dest_planet)

        d = distance(launch_planet, dest_planet)
        if dest_turn is None:
            dest_turn = 0
        dest_turn = max(dest_turn, self.current_turn + d/2)

        nships = min(nships, launch_planet.nships)

        if not nships:
            launch_planet.nships -= nships

            self.deployments.append(Deployment(launch_player, launch_planet, dest_planet, dest_turn, nships, launch_planet.killpct))
            return 'deployed'
        else:
            return 'no ships'

    def generate_planets(self):
        # name, x, y, prod, killpct, owner, nships
        nplayers = len(self.players)
        for i, (name, pl) in enumerate(self.players.items()):
            planet_name = planet_names[i]
            self.planets[name] = Planet(name, rand(map_width), rand(map_height), 10, 40, pl)

        for name in planet_names[nplayers:]:
            self.planets[name] = Planet(name, rand(map_width), rand(map_height), rand(10), rand(40))


class Player:
    def __init__(self, name, md5_password, sessionid):
        self.number = None
        self.name = name
        self.md5_password = md5_password
        self.sessionid = sessionid
        self.ready = False

    def as_tuple(self):
        return (self.number, self.name, player_colors[self.number], self.ready)


class Planet:
    def __init__(self, name, x, y, prod, killpct, owner=None):
        self.name = name
        self.x = x
        self.y = y
        self.prod = prod
        self.killpct = killpct
        self.owner = owner
        self.nships = prod

    def as_tuple(self):
        return (self.name, self.x, self.y, self.prod, self.killpct, self.owner, self.nships)


class Deployment:
    def __init__(self, launch_player, launch_planet, dest_planet, dest_turn, nships, killpct):
        self.launch_player = launch_player
        self.launch_planet = launch_planet
        self.dest_planet = dest_planet
        self.dest_turn = dest_turn
        self.nships = nships
        self.killpct = killpct

    def as_tuple(self):
        return (self.launch_player, self.launch_planet, self.dest_planet, self.dest_turn, self.nships, self.killpct)

### networking via simple HTTP

class HTTPException(Exception):
    def __init__(self, errcode, text):
        super().__init__(text)
        self.errcode = errcode


class WSIServer(http.server.HTTPServer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.game = Game()
        self.sessions = {}  # sessionid -> Player
        self.users = {}     # username -> Player


class WSIHandler(http.server.BaseHTTPRequestHandler):
    def generic_handler(self, reqtype, path, data, **kwargs):
        fields = urllib.parse.parse_qs(data)
        fields.update(kwargs)

        toplevel = path.split('/')[1]
        if toplevel:
            try:
                sessions = fields.get('session')
                pl = self.server.sessions.get(sessions[0]) if sessions else None

                if not pl:
                    username = fields.get('username')
                    if username:
                        username = username[0]
                        if username in self.server.users:
                            pl = self.server.users[username]
                            if fields['password'][0] != pl.md5_password:
                                raise HTTPException(403, 'Sorry, wrong password')
                        else:
                            sessionid = uuid.uuid1().hex
                            pl = Player(username, fields['password'][0], sessionid)
                            self.server.sessions[sessionid] = pl
                            self.server.users[username] = pl

                ret = getattr(self.server.game, '%s_%s' % (reqtype, toplevel))(pl, **fields)

                if isinstance(ret, list) or isinstance(ret, dict):
                    ret = json.dumps(ret)
                # else leave as string

                self.send_response(200)
                self.send_header('Content-type', 'text/json')
                self.end_headers()

                self.wfile.write(ret.encode('utf-8'))
            except HTTPException as e:
                self.send_response(e.errcode)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                self.wfile.write(str(e).encode('utf-8'))
            except Exception as e:
                import traceback
                print(traceback.format_exc())
                self.send_response(404)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                self.wfile.write(str(e).encode('utf-8'))

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        return self.generic_handler('GET', parsed_url.path, parsed_url.query)
        query = urllib.parse.parse_qs(parsed_url.query)

    def do_POST(self):
        length = int(self.headers['content-length'])
        field_data = self.rfile.read(length).decode('utf-8')
        return self.generic_handler('POST', self.path, field_data)


def main():
    server = WSIServer(('', 8080), WSIHandler)

    print('http://localhost:8080')
    server.serve_forever()


if __name__ == '__main__':
    main()

