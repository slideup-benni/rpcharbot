import json
import os
import re
import sqlite3
import time
from mimetypes import guess_extension
from pathlib import Path

import requests

from modules.kik_user import User


class CharacterPersistentClass:

    STATUS_NONE = 0
    STATUS_SET_PICTURE = 1
    STATUS_DYN_MESSAGES = 2

    def __init__(self, config, bot_username):
        self.connection = None
        self.cursor = None  # type: sqlite3.Cursor
        self.config = config
        self.bot_username = bot_username
        self.database_path = CharacterPersistentClass.get_database_path_from_config(config)

        if not os.path.exists(self.database_path):
            self.create_database()

    def __del__(self):
        if self.connection is not None:
            self.connection.commit()
            self.connection.close()

    def connect_database(self):
        if self.connection is None:
            self.connection = sqlite3.connect(self.database_path)
            self.connection.row_factory = sqlite3.Row
            self.cursor = self.connection.cursor()

    def commit(self):
        if self.connection is not None:
            self.connection.commit()

    @staticmethod
    def get_min_char_id():
        return 1

    @staticmethod
    def get_database_path_from_config(config):
        return config.get("DatabasePath", "{home}/database.db").format(home=str(Path.home()))

    def get_next_fee_char_id(self, user_id):
        self.connect_database()

        self.cursor.execute((
            "SELECT char_id "
            "FROM characters "
            "WHERE user_id=? "
            "ORDER BY char_id "
            "LIMIT 1 "
        ), [user_id])

        min_char_id = self.cursor.fetchone()
        if min_char_id is None or int(min_char_id['char_id']) != self.get_min_char_id():
            return self.get_min_char_id()

        self.cursor.execute((
            "SELECT char_id + 1 AS new_char_id "
            "FROM characters ch "
            "WHERE NOT EXISTS ( "
            "    SELECT  NULL "
            "    FROM    characters mi "
            "    WHERE   mi.char_id = ch.char_id + 1 AND mi.user_id=ch.user_id"
            ") AND ch.user_id=?"
            "ORDER BY char_id "
            "LIMIT 1 "
        ), [user_id])

        next_free_char_id = self.cursor.fetchone()
        if next_free_char_id is None:
            return self.get_min_char_id()
        return int(next_free_char_id['new_char_id'])

    def add_char(self, user_id, creator_id, text):
        self.connect_database()

        next_char_id = self.get_next_fee_char_id(user_id)
        data = (user_id, next_char_id, text, creator_id, int(time.time()))
        self.cursor.execute((
            "INSERT INTO characters "
            "(user_id, char_id, text, creator_id, created) "
            "VALUES (?, ?, ?, ?, ?)"
        ), data)
        return next_char_id

    def change_char(self, user_id, creator_id, text, char_id=None):
        self.connect_database()

        if char_id is None:
            char_id = self.get_min_char_id()

        if self.get_char(user_id, char_id) is None:
            return False

        data = (user_id, char_id, text, creator_id, int(time.time()))
        self.cursor.execute((
            "INSERT INTO characters "
            "(user_id, char_id, text, creator_id, created) "
            "VALUES (?, ?, ?, ?, ?)"
        ), data)

    def set_char_pic(self, user_id, creator_id, pic_url, char_id=None):
        self.connect_database()

        timestamp = int(time.time())

        if char_id is None:
            char_id = self.get_min_char_id()

        picture_path = self.config.get("PicturePath", "{home}/pictures").format(home=str(Path.home()))
        file_wo_ext = "{}/{}-{}-{}-{}".format(picture_path, user_id, creator_id, char_id, timestamp)
        file_tmp = file_wo_ext + ".tmp"
        with open(file_tmp, 'wb') as handle:
            response = requests.get(pic_url, stream=True)

            for block in response.iter_content(1024):
                if not block:
                    break

                handle.write(block)

        handle.close()

        if response.status_code != 200:
            os.remove(file_tmp)
            return False

        try:
            ext = guess_extension(response.headers['content-type'].split()[0].rstrip(";"))
        except KeyError:
            ext = ".jpg"

        if ext == ".jpe":
            ext = ".jpg"

        os.rename(file_tmp, file_wo_ext + ext)

        data = (user_id, char_id, file_wo_ext + ext, creator_id, int(time.time()))
        self.cursor.execute((
            "INSERT INTO character_pictures "
            "(user_id, char_id, picture_filename, creator_id, created) "
            "VALUES (?, ?, ?, ?, ?)"
        ), data)

        return True

    def move_char(self, from_user_id, to_user_id, from_char_id=None):
        self.connect_database()

        if from_char_id is None:
            from_char_id = self.get_min_char_id()

        to_char_id = self.get_next_fee_char_id(to_user_id)

        data = (to_user_id, to_char_id, from_user_id, from_char_id)
        self.cursor.execute((
            "UPDATE characters "
            "SET user_id=?, char_id=? "
            "WHERE user_id LIKE ? AND char_id=?"
        ), data)

        return to_char_id

    def remove_char(self, user_id, deletor_id, char_id=None):
        self.connect_database()

        if char_id is None:
            char_id = self.get_min_char_id()

        if self.get_char(user_id, char_id) is None:
            return False

        data = (deletor_id, int(time.time()), user_id, char_id)
        self.cursor.execute((
            "UPDATE characters "
            "SET deletor_id=?, deleted=? "
            "WHERE user_id LIKE ? AND char_id=?"
        ), data)

    def remove_last_char_change(self, user_id, deletor_id, char_id=None):
        self.connect_database()

        if char_id is None:
            char_id = self.get_min_char_id()

        if self.get_char(user_id, char_id) is None:
            return False

        data = (deletor_id, int(time.time()), user_id, char_id)
        self.cursor.execute((
            "UPDATE characters "
            "SET deletor_id=?, deleted=? "
            "WHERE id = ("
            "    SELECT id "
            "    FROM characters "
            "    WHERE user_id LIKE ? AND char_id=? AND deleted IS NULL "
            "    ORDER BY created DESC "
            "    LIMIT 1"
            ")"
        ), data)

    def get_first_char_id(self, user_id):
        self.connect_database()

        self.cursor.execute((
            "SELECT MIN(char_id) AS min_char_id " 
            "  FROM  characters " 
            "  WHERE user_id = ? AND deleted IS NULL "
        ), [user_id])

        row = self.cursor.fetchone()
        if row is None or row["min_char_id"] is None:
            return None
        return int(row["min_char_id"])

    def get_char(self, user_id, char_id=None):
        self.connect_database()

        if char_id is None:
            char_id = self.get_first_char_id(user_id)

        self.cursor.execute((
            "SELECT id, user_id, char_id, text, creator_id, created, "
            "    (SELECT MIN(char_id) "
            "        FROM  characters AS c1 "
            "        WHERE c1.user_id = c.user_id AND c1.deleted IS NULL AND c1.char_id > c.char_id) AS next_char_id, "
            "    (SELECT MAX(char_id) "
            "        FROM  characters AS c2 "
            "        WHERE c2.user_id = c.user_id AND c2.deleted IS NULL AND c2.char_id < c.char_id) AS prev_char_id "
            "FROM  characters AS c "
            "WHERE user_id LIKE ? AND char_id=? AND deleted IS NULL "
            "ORDER BY created DESC "
            "LIMIT 1"
        ), [user_id, char_id])

        return self.cursor.fetchone()

    def get_char_pic_url(self, user_id, char_id):
        self.connect_database()

        if char_id is None:
            char_id = self.get_first_char_id(user_id)

        self.cursor.execute((
            "SELECT picture_filename, active "
            "FROM  character_pictures "
            "WHERE user_id LIKE ? AND char_id=? AND deleted IS NULL "
            "ORDER BY created DESC "
            "LIMIT 1"
        ), [user_id, char_id])

        pic_data = self.cursor.fetchone()
        if pic_data is None:
            return None

        if pic_data['active'] == 0:
            return False

        return "{}:{}/picture/{}".format(
            self.config.get("RemoteHostIP", "www.example.com"),
            self.config.get("RemotePort", "8080"),
            os.path.basename(pic_data['picture_filename'])
        )

    def get_all_user_chars(self, user_id):
        self.connect_database()

        self.cursor.execute((
            "SELECT id, char_id, text, creator_id, MAX(created) AS created "
            "FROM characters "
            "WHERE user_id LIKE ? AND deleted IS NULL "
            "GROUP BY char_id"
        ), [user_id])
        chars = self.cursor.fetchall()
        return chars

    def list_all_users_with_chars(self, page=1, limit=15, list_all=False):
        self.connect_database()

        query = (
            "SELECT id, user_id, MAX(char_id) AS chars_cnt, created "
            "FROM characters "
            "WHERE deleted IS NULL "
            "GROUP BY user_id "
            "ORDER BY created DESC "
        )

        if list_all is False:
            query += "LIMIT ?,? "

        self.cursor.execute(query, [(page - 1) * limit, limit + 1] if list_all is False else [])
        return self.cursor.fetchall()

    def find_char(self, name, user_id):
        self.connect_database()

        self.cursor.execute((
            "SELECT id, char_id, text, creator_id, MAX(created) AS created "
            "FROM characters "
            "WHERE user_id=? AND deleted IS NULL AND text LIKE ? "
            "GROUP BY user_id, char_id"
        ), [user_id, "%"+name+"%"])
        chars_raw = self.cursor.fetchall()
        chars = []

        for char in chars_raw:
            if re.search(r".*?name(.*?):[^a-z]*?{}[^a-z]*?".format(re.escape(name)), char['text'], re.MULTILINE+re.IGNORECASE) is not None:
                chars.append(char)

        return chars

    def search_char(self, query, query_key="name", user_id=None):
        self.connect_database()

        if user_id is None:
            self.cursor.execute((
                "SELECT id, user_id, char_id, text, creator_id, MAX(created) AS created "
                "FROM characters "
                "WHERE deleted IS NULL AND text LIKE ? "
                "GROUP BY user_id, char_id"
            ), ["%"+query+"%"])

        else:
            self.cursor.execute((
                "SELECT id, user_id, char_id, text, creator_id, MAX(created) AS created "
                "FROM characters "
                "WHERE deleted IS NULL AND user_id=? AND text LIKE ? "
                "GROUP BY user_id, char_id"
            ), [user_id, "%" + query + "%"])

        chars_raw = self.cursor.fetchall()
        chars = []

        for char in chars_raw:
            if re.search(r".*?{}(.*?):[^a-z]*?{}[^a-z]*?".format(re.escape(query_key), re.escape(query)), char['text'], re.MULTILINE | re.IGNORECASE) is not None:
                chars.append(char)

        return chars

    def update_user(self, user: User, as_request=True):
        self.connect_database()

        if user.get_db_id() is None:
            self.cursor.execute((
                "INSERT INTO users "
                "(user_id, bot_id, first_name, last_name, is_user_id, is_char_id, status, authed_since, authed_by, is_admin, last_request, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            ), [user["user_id"],
                self.bot_username,
                user["first_name"],
                user["last_name"],
                user["is_user_id"],
                user["is_char_id"],
                user["status"],
                user["authed_since"],
                user["authed_by"],
                user["is_admin"],
                int(time.time()) if as_request is True else user["last_request"],
                int(time.time())])
        else:
            self.cursor.execute((
                "UPDATE users "
                "SET first_name = ?, "
                "    last_name = ?, "
                "    is_user_id = ?, "
                "    is_char_id = ?, "
                "    status = ?, "
                "    authed_since = ?, "
                "    authed_by = ?, "
                "    is_admin = ?, "
                "    last_request = ? "
                "WHERE user_id LIKE ? AND bot_id LIKE ?"
            ), [user["first_name"], user["last_name"], user["is_user_id"], user["is_char_id"], user["status"], user["authed_since"], user["authed_by"],
                user["is_admin"], int(time.time()) if as_request is True else user["last_request"], user["user_id"], self.bot_username])



    def get_user(self, user_id):
        self.connect_database()

        self.cursor.execute((
            "SELECT * "
            "FROM users "
            "WHERE user_id LIKE ? AND "
            "    bot_id LIKE ? "
            "LIMIT 1"
        ), [user_id, self.bot_username])

        return self.cursor.fetchone()

    def get_kik_user(self, user_id):
        self.connect_database()

        self.cursor.execute((
            "SELECT * "
            "FROM kik_user_response "
            "WHERE user_id LIKE ? "
            "ORDER BY created DESC "
            "LIMIT 1"
        ), [user_id])

        return self.cursor.fetchone()

    def add_kik_user_data(self, user_id, kik_user):
        """

        :type user_id: str
        :type kik_user: kik.User
        """
        self.connect_database()

        self.cursor.execute((
            "INSERT INTO kik_user_response "
            "(bot_id, user_id, first_name, last_name, profile_pic_url, profile_pic_last_modified, timezone, plain_response, created) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        ), [self.bot_username, user_id, kik_user.first_name, kik_user.last_name, kik_user.profile_pic_url, kik_user.profile_pic_last_modified, kik_user.timezone,
            json.dumps(kik_user.to_json()), int(time.time())])

        return self.get_kik_user(user_id)

    def set_static_message(self, command, response):
        self.connect_database()

        static_message = self.get_static_message(command)
        if static_message is None:
            self.cursor.execute((
                "INSERT INTO static_messages "
                "(command, response) "
                "VALUES (?, ?) "
            ), [command, response])
        else:
            self.cursor.execute((
                "UPDATE static_messages "
                "SET response = ? "
                "WHERE command LIKE ? "
            ), [response, command])

        return self.get_static_message(command)

    def set_static_message_keyboard(self, command, keyboard):
        self.connect_database()

        static_message = self.get_static_message(command)
        if static_message is not None:
            self.cursor.execute((
                "UPDATE static_messages "
                "SET response_keyboards = ? "
                "WHERE command LIKE ? "
            ), [json.dumps(keyboard), command])

        return self.get_static_message(command)

    def set_static_message_alt_commands(self, command, alt_commands):
        self.connect_database()

        static_message = self.get_static_message(command)
        if static_message is not None:
            self.cursor.execute((
                "UPDATE static_messages "
                "SET alt_commands = ? "
                "WHERE command LIKE ? "
            ), [json.dumps(alt_commands), command])

        return self.get_static_message(command)

    def get_static_message(self, command):
        self.connect_database()

        self.cursor.execute((
            "SELECT * "
            "FROM static_messages "
            "WHERE command LIKE ? OR alt_commands LIKE ? "
            "LIMIT 1"
        ), [command, "%\""+command+"\"%"])

        return self.cursor.fetchone()

    def get_all_static_messages(self):
        self.connect_database()

        self.cursor.execute((
            "SELECT * "
            "FROM static_messages"
        ))

        return self.cursor.fetchall()

    def create_database(self):
        print("Datenbank {} nicht vorhanden - Datenbank wird anglegt.".format(os.path.basename(self.database_path)))
        connection = sqlite3.connect(self.database_path)
        cursor = connection.cursor()

        cursor.executescript(open('database.sql', 'r').read())

        connection.commit()
        connection.close()
        print("Datenbank {} angelegt".format(os.path.basename(self.database_path)))
