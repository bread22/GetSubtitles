# coding: utf-8

import os
import zipfile
from os import path
from tempfile import TemporaryDirectory, NamedTemporaryFile
from shutil import make_archive

from io import BytesIO
from shutil import get_terminal_size

import rarfile
from guessit import guessit

from pyunpack import Archive

from getsub.constants import SUB_FORMATS, ARCHIVE_TYPES, VIDEO_FORMATS, PREFIX


class ProgressBar:
    def __init__(self, prefix_info, title="", total="", count_time=0):
        self.title = title
        self.total = total
        self.prefix_info = prefix_info

    def refresh(self, cur_len):
        terminal_width = get_terminal_size().columns  # 获取终端宽度
        info = "%s '%s'...  %.2f%%" % (
            self.prefix_info,
            self.title,
            cur_len / self.total * 100,
        )
        while len(info) > terminal_width - 20:
            self.title = self.title[0:-4] + "..."
            info = "%s '%s'...  %.2f%%" % (
                self.prefix_info,
                self.title,
                cur_len / self.total * 100,
            )
        end_str = "\r" if cur_len < self.total else "\n"
        print(info, end=end_str)


def get_videos(raw_path, store_path="", identifier=""):
    """
    传入视频名称或路径，构造一个包含视频路径和是否存在字幕信息的字典返回
    若指定 store_path ，则是否存在字幕会在 store_path 中查找

    params:
        raw_path: str, video path/name or a directory path
        store_path: str, subtitles store path
        identifier: str, identifier in subtitles' names
    return:
        video_dict: dict
            key: video file name, without path
            value: "video_path" - str, abspath of video's parent directory
                   "store_path" - str, subtitles' store path (abspath)
                   "has_subtitle" - bool
    """

    def _sub_exists(video_name, store_path):
        sub_types = [identifier + sub_type for sub_type in SUB_FORMATS]
        for sub_type in sub_types:
            if path.exists(path.join(store_path, video_name + sub_type)):
                return True
        return False

    raw_path = raw_path.replace('"', "")
    store_path = store_path.replace('"', "")
    if store_path:
        store_path = path.abspath(store_path)
    store_path_files = []

    if not path.isdir(store_path):
        if store_path:
            print("store path is invalid: " + store_path)
        store_path = ""
    else:
        print("subtitles will be saved to: " + store_path)
        for root, dirs, files in os.walk(store_path):
            store_path_files.extend(files)

    video_dict = dict()

    if path.isdir(raw_path):  # directory
        for root, dirs, files in os.walk(raw_path):
            s_path = path.abspath(root) if not store_path else store_path
            for file in files:
                v_name, v_type = path.splitext(file)
                if v_type not in VIDEO_FORMATS:
                    continue
                sub_exists = _sub_exists(v_name, s_path)
                video_dict[file] = {
                    "video_path": path.abspath(root),
                    "store_path": s_path,
                    "has_subtitle": sub_exists,
                }
    elif path.isabs(raw_path):  # video's absolute path
        v_path, v_raw_name = path.split(raw_path)
        v_name = path.splitext(v_raw_name)[0]
        s_path = v_path if not store_path else store_path
        sub_exists = _sub_exists(v_name, s_path)
        video_dict[v_raw_name] = {
            "video_path": v_path,
            "store_path": s_path,
            "has_subtitle": sub_exists,
        }
    else:  # single video name, no path
        s_path = os.getcwd() if not store_path else store_path
        video_dict[raw_path] = {
            "video_path": raw_path,
            "store_path": s_path,
            "has_subtitle": False,
        }

    return video_dict


def _print_and_choose(items):

    for i, item in enumerate(items):
        print(PREFIX + " %3s) %s" % (i, item))

    choice = None
    while choice is None:
        try:
            print(PREFIX)
            choice = input(PREFIX + "  choose: ")
            choice = int(choice)
            assert choice < len(items)
        except ValueError:
            print(PREFIX + "  only numbers accepted")
            choice = None
        except AssertionError:
            print(PREFIX + "  ", end="\r")
            print("choice %d not within the range" % choice)
            choice = None

    return choice


def choose_archive(sub_dict, sub_num=5, query=True):
    """
    传入候选字幕字典，返回选择的字幕包名称，字幕包下载地址

    params:
        sub_dict: dict, check downloader.py
        sub_num: int, maximum number of subtitles
        query: bool, return first sub if False
    return:
        exit: bool
        chosen_subs: str, subtitle name
    """

    exit = False

    if not query:
        chosen_sub = list(sub_dict.keys())[0]
        return exit, chosen_sub

    items = []
    items.append("Exit. Not downloading any subtitles.")
    for i, key in enumerate(sub_dict.keys()):
        if i == sub_num:
            break
        lang_info = ""
        lang_info += "【简】" if 4 & sub_dict[key]["lan"] else "      "
        lang_info += "【繁】" if 2 & sub_dict[key]["lan"] else "      "
        lang_info += "【英】" if 1 & sub_dict[key]["lan"] else "      "
        lang_info += "【双】" if 8 & sub_dict[key]["lan"] else "      "
        sub_info = "%s  %s" % (lang_info, key)
        items.append(sub_info)

    choice = _print_and_choose(items)
    if choice == 0:
        exit = True
        return exit, []

    return exit, list(sub_dict.keys())[choice - 1]


def choose_subtitle(subtitles):
    """
    传入字幕列表，返回选择字幕名

    params:
        subtitles: list of str
    return:
        subname: str
    """

    items = []
    for subtitle in subtitles:
        try:
            # zipfile: Historical ZIP filename encoding
            subtitle = subtitle.encode("cp437").decode("gbk")
        except Exception:
            pass
        items.append(subtitle)

    choice = _print_and_choose(items)

    return subtitles[choice]


def _compute_subtitle_score(video_detail, subname):
    """
    计算字幕分数

    params:
        video_detail: dict, result of guessit
        subname: str
    return:
        score: int, return -1 if not match with videos
    """

    video_name = video_detail["title"].lower()
    season = str(video_detail.get("season"))
    episode = str(video_detail.get("episode"))
    year = str(video_detail.get("year"))
    vtype = str(video_detail.get("type"))

    subname = subname.lower()
    score = 0

    sub_name_info = guessit(subname)
    if sub_name_info.get("title"):
        sub_title = sub_name_info["title"].lower()
    else:
        sub_title = ""
    sub_season = str(sub_name_info.get("season"))
    sub_episode = str(sub_name_info.get("episode"))
    sub_year = str(sub_name_info.get("year"))

    if vtype == "movie":
        if year == sub_year:
            score += 1
        if video_name == sub_title:
            score += 1
        elif sub_title != "":
            return -1
    else:
        if video_name == sub_title:
            if not (season == sub_season and episode == sub_episode):
                return -1  # title match, episode not match
            else:
                score += 1  # title and episode match
        elif season == sub_season and episode == sub_episode:
            # title not match, episode match
            if sub_title != "":
                return -1
        else:
            return -1  # title and episode not match

    if "简体" in subname or "chs" in subname or ".gb." in subname:
        score += 2
    if "繁体" in subname or "cht" in subname or ".big5." in subname:
        pass
    if "chs.eng" in subname or "chs&eng" in subname:
        score += 2
    if "中英" in subname or "简英" in subname or "双语" in subname or "简体&英文" in subname:
        score += 4

    score += ("ass" in subname or "ssa" in subname) * 2
    score += ("srt" in subname) * 1

    return score


def guess_subtitle(sublist, video_detail):
    """
    传入字幕列表，视频信息，返回得分最高字幕名

    params:
        sublist: list of str
        video_detail: result of guessit
    return:
        success: bool
        subname: str
    """

    if not sublist:
        return False, None

    scores, subs = [], []
    for one_sub in sublist:
        _, ftype = path.splitext(one_sub)
        if ftype not in SUB_FORMATS:
            continue
        subs.append(one_sub)
        subname = path.split(one_sub)[-1]  # extract subtitle name
        try:
            # zipfile:/Lib/zipfile.py:1211 Historical ZIP filename encoding
            # try cp437 encoding
            subname = subname.encode("cp437").decode("gbk")
        except Exception:
            pass
        score = _compute_subtitle_score(video_detail, subname)
        scores.append(score)

    max_score = max(scores)
    max_pos = scores.index(max_score)
    return max_score > 0, subs[max_pos]


def get_file_list(data, datatype):
    """
    传入一个压缩文件控制对象，读取对应压缩文件内文件列表

    params:
        data: binary data of an archive file
        datatype: str, file type
    return:
        sub_lists_dict: dict, {subname: file_handler}
    """

    sub_buff = BytesIO(data)

    if datatype == ".7z":
        try:
            sub_buff.seek(0)
            sub_buff = _convert_7z_to_zip(sub_buff)
            datatype = ".zip"
        except Exception:
            datatype = ".zip"  # try with zipfile
    if datatype == ".zip":
        try:
            sub_buff.seek(0)
            file_handler = zipfile.ZipFile(sub_buff, mode="r")
        except Exception:
            datatype = ".rar"  # try with rarfile
    if datatype == ".rar":
        sub_buff.seek(0)
        file_handler = rarfile.RarFile(sub_buff, mode="r")

    sub_lists_dict = dict()

    for one_file in file_handler.namelist():

        if path.splitext(one_file)[-1] in SUB_FORMATS:
            sub_lists_dict[one_file] = file_handler
            continue

        if path.splitext(one_file)[-1] in ARCHIVE_TYPES:
            data = file_handler.read(one_file)
            datatype = path.splitext(one_file)[-1]
            sub_lists_dict.update(get_file_list(data, datatype))

    return sub_lists_dict


def process_archive(
    video_name,
    video_info,
    archive_data,
    datatype,
    both=False,
    choose=False,
    identifier="",
):
    """
    解压字幕包，返回解压字幕名列表

    params:
        video_name: str, video file name
        video_info: dict, result of get_videos
        archive_data: binary archive data
        datatype: str, archive type
        both: bool, whether save two subtitles (.ass and .srt)
        choose: bool, whether manually choose subtitles in the archive
        identifier: str, segment inserted into subtitles' names
                    eg. identifier=".zh" "video.srt" => "video.zh.srt"
    return:
        error: str, error message
        extract_subs: list, [<subname, subtype>, ...]
    """

    error = ""

    if datatype not in ARCHIVE_TYPES:
        error = "unsupported file type " + datatype
        return error, []

    sub_lists_dict = get_file_list(archive_data, datatype)

    if len(sub_lists_dict) == 0:
        error = "no subtitle in this archive"
        return error, []

    # get subtitles to extract
    if not choose:
        video_detail = guessit(video_name)
        success, sub_name = guess_subtitle(list(sub_lists_dict.keys()), video_detail)
        if not success:
            error = "no guess result in auto mode"
            return error, []
    else:
        sub_name = choose_subtitle(list(sub_lists_dict.keys()))

    # build new names
    sub_title, sub_type = path.splitext(sub_name)
    extract_subs = [[sub_name, sub_type]]
    if both:
        another_sub_type = ".srt" if sub_type == ".ass" else ".ass"
        another_sub = sub_name.replace(sub_type, another_sub_type)
        another_sub = path.basename(another_sub)
        for subname in list(sub_lists_dict.keys()):
            if another_sub in subname:
                extract_subs.append([subname, another_sub_type])
                break
        else:
            print(PREFIX + " no %s subtitles in this archive" % another_sub_type)

    v_name_without_format = path.splitext(video_name)[0]

    # delete existed subtitles
    for one_sub_type in SUB_FORMATS:
        delete_name = v_name_without_format + identifier + one_sub_type
        delete_file = path.join(video_info["store_path"], delete_name)

        if path.exists(delete_file):
            os.remove(delete_file)

    # extract subtitles
    for one_sub, one_sub_type in extract_subs:
        sub_new_name = v_name_without_format + identifier + one_sub_type
        extract_path = path.join(video_info["store_path"], sub_new_name)
        with open(extract_path, "wb") as sub:
            file_handler = sub_lists_dict[one_sub]
            sub.write(file_handler.read(one_sub))

    for extract_sub_name, extract_sub_type in extract_subs:
        extract_sub_name = extract_sub_name.split("/")[-1]
        try:
            # zipfile: Historical ZIP filename encoding
            # try cp437 encoding
            extract_sub_name = extract_sub_name.encode("cp437").decode("gbk")
        except Exception:
            pass
        try:
            print(PREFIX + " " + extract_sub_name)
        except UnicodeDecodeError:
            print(PREFIX + " " + extract_sub_name.encode("gbk"))
    return error, extract_subs


def _convert_7z_to_zip(sub_buff):
    """ Convert 7z buff to zip buff

    This is for Synology compatibility, which cannot install pylzma.
    So have to fall back to crude method provided by pyunpack/patool.
    Convert the buff to a zip buff for minimizing code structure change.

    1. write to a 7z file
    2. extract this 7z file
    3. add content of extracted directory to a new zip file
    4. read the new zip file and return

    Arguments:
        sub_buff {byte} -- 7z file buff

    Returns:
        [byte] -- zip file buff
    """
    archivefile_7z = NamedTemporaryFile()
    archivefile_zip = NamedTemporaryFile()
    tempdir = TemporaryDirectory()

    # extract files from 7z IO buffer
    with open(archivefile_7z.name, 'wb') as f:
        f.write(sub_buff.read())
    archive = Archive(archivefile_7z.name)
    archive.extractall(tempdir.name)

    # get file lists and add them to zip archive
    make_archive(archivefile_zip.name, 'zip', tempdir.name)

    # read zip file and return
    with open(archivefile_zip.name + '.zip', 'rb') as f:
        data = f.read()

    return BytesIO(data)
