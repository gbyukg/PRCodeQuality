#!/usr/bin/env python
# -*- coding: utf-8 -*-
import tempfile

##############################################
#
# Getting Github PR information
#
##############################################
'''
工具集合
'''

import os
import sys
import errno
import requests
from argparse import ArgumentParser
from json import loads as jsloads
from json import dumps as jsdumps
from shlex import split as shsplit
from subprocess import Popen, PIPE
import xml.etree.ElementTree as ET

GITHUB_API_URL = os.environ['GITHUB_API_URL']
GITHUB_REPO_USER = os.environ['GITHUB_REPO_USER']
GITHUB_REPO_NAME = os.environ['GITHUB_REPO_NAME']


def fetch_api(url, githubToken, **cus_headers):
    headers = {
        'user-agent': 'zzlzhang',
        'Content-Type': 'application/json',
        'Authorization': "token {0:s}".format(githubToken),
    }
    for key, value in cus_headers.get('headers', {}).items():
        headers[key] = value

    postData = cus_headers.get('data', {})

    method_to_call = getattr(requests, cus_headers.get('method', 'get'))
    response = requests.post(url, headers=headers, data=jsdumps(postData)) if postData else method_to_call(url, headers=headers)

    if not response.ok:
        response.raise_for_status()
    return response.text


def get_user(github_token):
    assert github_token, "Error: Github token can not be empty."

    apiUrl = "{0:s}/user".format(GITHUB_API_URL)
    return fetch_api(apiUrl, github_token)


def create_pr_status(args):
    assert args.status_url, "Error: Github status url can not be empty"
    assert args.github_token, "Error: Github token can not be empty."
    # pending, success, error, or failure
    assert args.state, "Error: Please provide status state"
    assert args.target_url, "Error: Please provide status target_url"
    assert args.description, "Error: Please provide status description"
    assert args.context, "Error: Please provide status context"

    statusData = {
        "state": args.state,
        "target_url": args.target_url,
        "description": args.description,
        "context": args.context
    }
    fetch_api(args.status_url, args.github_token, data=statusData)


def get_pr_info(args):
    ''' Get PR informations '''
    github_token = args.github_token
    assert github_token, "Error: Github token can not be empty."

    pr_number = args.pr_number
    assert pr_number, "Error: PR number is empty."

    apiUrl = "{0:s}/repos/{1:s}/{2:s}/pulls/{3:d}".format(
        GITHUB_API_URL,
        GITHUB_REPO_USER,
        GITHUB_REPO_NAME,
        pr_number
    )
    # 直接将结果打印出来, 结果将被 groovy 处理
    print(fetch_api(url=apiUrl, githubToken=github_token))


def generate_pr_patch(args):
    '''
    将PR中的所有文件的 patch 信息生成到文件中
    '''
    github_token = args.github_token
    assert github_token, "Error: Github token can not be empty."

    assert args.base_sha, "Error: Please provide base SHA."
    assert args.head_sha, "Error: Please provide head SHA."

    TMP_DIR = os.environ['TMP_DIR']
    assert TMP_DIR, "Error: Please provide template directory."

    apiUrl = "{0:s}/repos/{1:s}/{2:s}/compare/{3:s}...{4:s}".format(
        GITHUB_API_URL,
        GITHUB_REPO_USER,
        GITHUB_REPO_NAME,
        args.base_sha,
        args.head_sha
    )
    data = jsloads(fetch_api(
        url=apiUrl,
        githubToken=github_token,
        headers={'Content-Type': 'application/vnd.github.v3.diff'}
        ))
    fileList = ''
    for prfile in data['files']:
        # 不需要记录被移除的文件
        # 并且只检测 PHP 和 JS 文件
        if prfile['status'] == 'removed' or os.path.splitext(prfile[u'filename'])[1] not in ('.php', '.js'):
            continue
        # sugarcrm/custom/include/javascript/Help.js
        filename = "{0:s}/{1:s}".format(TMP_DIR, prfile[u'filename'])

        filedir = os.path.dirname(filename)
        if not os.path.exists(filedir):
            try:
                os.makedirs(filedir)
            except OSError as exc:
                if exc.errno != errno.EEXIST:
                    raise

        with open(filename, 'w') as f:
            f.write(prfile[u'patch'])
        fileList = "{0:s}{1:s} ".format(fileList, prfile[u'filename'])

    print(fileList)


def write_context_to_file(cmds, file_name=None, acceptExitCode=(0,)):
    msg = None
    return_code = 0
    if file_name is None:
        o_file = PIPE
    else:
        o_file = open(file_name, 'w+')

    try:
        proc = Popen(
            args=shsplit(cmds),
            bufsize=8192,
            stdout=o_file,
            stderr=PIPE,
            close_fds=True,
        )
        procCom = proc.communicate()
        assert proc.returncode in acceptExitCode, procCom[1]
    except OSError as e:
        msg = """Subprocess execute failed
Error message:{0:s}
""".format(e.strerror)
        return_code = -1
    except Exception as e:
        msg = "{0:s}Error code: {1:d}".format(e.message, proc.returncode)
        return_code = -1
    finally:
        if return_code == 0:
            # 如果 file_name 为空, 则表示将结果返回给字符串
            if file_name is None:
                return procCom[0]
            else:
                o_file.close()
                return file_name
        print(msg)
        return '/dev/null'


def writeParentFile(sourceFile, base_sha, mango_work_tree):
    '''
    生成一个临时文件, 将文件修改前的版本写入到临时文件中
    '''
    tempFile = tempfile.mkstemp()[1]
    git_cmd = 'git --git-dir={0:s}/.git --work-tree={0:s} show {1:s}:{2:s}'.format(
        mango_work_tree,
        base_sha,
        sourceFile
    )
    return write_context_to_file(git_cmd, tempFile)
    # return tempFile


def getChangedLine(parentFile, currentFile):
    diff_cmd = "diff --unchanged-line-format='' --old-line-format='' --new-line-format='%dn ' {0:s} {1:s}".format(parentFile, currentFile)
    return write_context_to_file(diff_cmd, None, (0, 1))


def getDiffPosition(diffFileObj, lineNumber, position=-1, sourceLine=0):
    '''
    要使用PR review comment, 首先必须获取到 diff 文件的 行号(position)
    而不是源文件的行号
    '''

    bFound = False
    for line in diffFileObj:
        if line.startswith('-'):
            position += 1
            continue
        if line.startswith('@@ '):
            # @@ -13,6 +13,11
            # 获取源文件的起始行号
            # position 从 -1 开始计算, 因为只有第一个出现的 @@ 不会计算position
            # 其余的 @@ 全部都需要被计算到 position 中
            sourceLine = abs(int(line.split(" ")[1].split(',')[0]))
            bFound = True
            position += 1
            continue

        position += 1
        if not bFound:
            sourceLine += 1
        bFound = False
        if sourceLine == int(lineNumber):
            return (position, sourceLine)


def remove_review_comments(commentListUrl, githubToken):
    '''
    commentListUrl 包含每个 comment 的 URL 信息
    迭代 list 循环删除所有 comment
    '''
    for commentId in commentListUrl:
        fetch_api(commentId, githubToken, method='delete')


def get_review_comments(args):
    '''
    获取当前 token 用户的所有针对指定 PR 的comment
    '''
    assert args.user_login, "Error: User login ID can not be empty."

    commentListUrl = []

    apiUrl = "{0:s}/repos/{1:s}/{2:s}/pulls/{3:d}/comments".format(
        GITHUB_API_URL,
        GITHUB_REPO_USER,
        GITHUB_REPO_NAME,
        args.pr_number
    )
    response = jsloads(fetch_api(apiUrl, args.github_token))

    # 获取当前 token 用户下的所有 comments
    for com in response:
        commentListUrl.append(com['url']) if com['user']['login'] == args.user_login else ''
    print(commentListUrl) if commentListUrl else ''
    return commentListUrl


def create_review_comments(args):
    '''
    读取代码检测生成的 XML 文件
    在创建 comment 之前, 要先删除当前用户创建的所有在该 PR 下的 comment
    '''
    GITHUB_REPO_DIR = os.environ['GITHUB_REPO_DIR']
    assert GITHUB_REPO_DIR, "Error: Please provew git repository directory."

    TMP_DIR = os.environ['TMP_DIR']
    assert TMP_DIR, "Error: Please provide template directory."

    assert args.github_token, "Error: Github token can not be empty."
    assert args.pr_number, "Error: PR number is empty."
    assert args.fileName, "Error: XML file of code style check result file."

    errorContext = []

    user_info = jsloads(get_user(args.github_token))

    # 获取用户的 login 信息, 以便判断只删除当前用户的 comment
    args.user_login = user_info['login']
    # 获取当前用户的针对当前 PR 的所有 comments
    commentListUrl = get_review_comments(args)
    # 移除当前用户的所有 comments
    remove_review_comments(commentListUrl, args.github_token) if commentListUrl else ''

    try:
        tree = ET.parse(args.fileName)
    except IOError as exc:
        raise IOError("Error: {0:s} {1:s}".format(args.fileName, exc.strerror))
    except Exception as exc:
        raise exc
    root = tree.getroot()
    for sourceFile in root.iter('file'):
        # 获取查找文件时返回的diff position 和 原始代码行号
        # 最初原始代码行号是通过 @@ -17,7 +17,7 获取: 17
        # getDiffPosition 返回后需要捕获当前已经获取到的行号,
        # 以便继续查找行号时没有 @@ -17,7 +17,7 相关信息也可以获取原始代码行号

        # position 从 -1 开始计算, 因为只有第一个出现的 @@ 不会计算position
        # 其余的 @@ 全部都需要被计算到 position 中
        positionList = [-1, 0]
        sourceFileName = sourceFile.attrib['name'].replace("{0:s}/".format(GITHUB_REPO_DIR), '')
        diffFile = "{0:s}/{1:s}".format(TMP_DIR, sourceFileName)
        with open(diffFile, 'r') as f:
            for errorList in sourceFile.iter('error'):
                # 每一行可能会出现多个错误, 如果下次循环的行号与上次返回的行号相同, 说明是同一行出现多个错误
                if int(positionList[1]) != int(errorList.attrib['line']):
                    positionList = getDiffPosition(f, errorList.attrib['line'], positionList[0], positionList[1])
                errorContext.append({
                    "path": sourceFileName,
                    "position": positionList[0],
                    "body": "[{0:s}]: Line {1:s} {2:s}".format(
                        errorList.attrib['severity'].upper(),
                        errorList.attrib['line'],
                        errorList.attrib['message']
                    ),
                })

    if len(errorContext):
        prComments = {
            # "body": "Codeing style check",
            "event": "REQUEST_CHANGES",
            "comments": errorContext
        }

        apiUrl = "{0:s}/repos/{1:s}/{2:s}/pulls/{3:d}/reviews".format(
            GITHUB_API_URL,
            GITHUB_REPO_USER,
            GITHUB_REPO_NAME,
            args.pr_number
        )
        fetch_api(apiUrl, args.github_token, data=prComments)


def parse_xml_result(args):
    '''
    解析生成的完整的代码检测 xml 文件, 移除并不是在本次PR中修改的行.
    '''
    GITHUB_REPO_DIR = os.environ['GITHUB_REPO_DIR']
    assert GITHUB_REPO_DIR, "Error: Please provew git repository directory."
    assert args.base_sha, "Error: Please provide base SHA"

    errorCount = 0
    removeFileNodeList = []

    try:
        tree = ET.parse(args.fileName)
    except IOError as exc:
        raise IOError("Error: {0:s} {1:s}".format(args.fileName, exc.strerror))
    except Exception as exc:
        raise exc

    root = tree.getroot()
    for sourceFile in root.iter('file'):
        sourceFileName = sourceFile.attrib['name'].replace("{0:s}/".format(GITHUB_REPO_DIR), '')
        print("   Checking {0:s}".format(sourceFileName))

        # 根据PR改动之前的内容生成临时文件
        # 由于使用 git 命令获取修改之前的文件时指定了 git 的工作目录, 所以 sourceFileName 需要以git为基准目录
        # 如果 base 中不存在, 则返回 /dev/null
        tempFile = writeParentFile(
            sourceFileName,
            args.base_sha,
            GITHUB_REPO_DIR
        )
        print("TEMPFILE: {0:s}".format(tempFile))

        # 获取改动的行号, sourceFile 需要使用决定路径
        changedLines = (getChangedLine(tempFile, sourceFile.attrib['name'])).split()
        # print(changedLines)

        removeErrorNodeList = []

        for errorLine in sourceFile.iter('error'):
            currentAttrib = errorLine.attrib
            if currentAttrib['line'] in changedLines:
                currentAttrib['filePath'] = sourceFileName
                # errorList.append(currentAttrib)
                print("[{0:s}] Line {1:s}: {2:s}".format(
                    currentAttrib['severity'],
                    currentAttrib['line'],
                    currentAttrib['message']
                ))
                errorCount += 1
            else:
                removeErrorNodeList.append(errorLine)
        # 必须单独列出来删除, 否则在上一个 foreach 将删除不干净
        for errorLine in removeErrorNodeList:
            sourceFile.remove(errorLine)

        if sourceFile.find('error') is None:
            removeFileNodeList.append(sourceFile)

    tree.write(args.fileName)
    sys.exit(errorCount)


def add_common_args(parser):
    ''' Add common args '''
    parser.add_argument('-t', '--token',
                        action='store',
                        dest='github_token',
                        required=True,
                        metavar='Github_Token',
                        help='Github Token')
    parser.add_argument('-p', '--pr-number',
                        action='store',
                        dest='pr_number',
                        metavar='PR_Number',
                        type=int,
                        required=True,
                        help='Pull Request number')


def get_args():
    ''' 参数解析 '''
    parser = ArgumentParser(prog="util")
    subparsers = parser.add_subparsers(prog='util')

    # 获取 PR 信息
    arg_pr_info = subparsers.add_parser('pr', help='patch help')
    arg_pr_info.add_argument(
        '--type',
        dest="type",
        type=str,
        default="get_pr_info",
        help='Get base and head files for a PR')
    add_common_args(arg_pr_info)

    # 生成PR中文件的补丁
    arg_pr_patch = subparsers.add_parser('patch', help='generate patch files')
    arg_pr_patch.add_argument(
        '--type',
        dest="type",
        type=str,
        default="generate_pr_patch",
        help='Generate patch for each file in the PR')
    arg_pr_patch.add_argument(
        '-t',
        '--token',
        action='store',
        dest='github_token',
        required=True,
        metavar='Github_Token',
        help='Github Token')
    arg_pr_patch.add_argument(
        '--base-sha',
        action='store',
        dest='base_sha',
        required=True,
        metavar='Base_sha',
        help='Base Token, used for compare command'
    )
    arg_pr_patch.add_argument(
        '--head-sha',
        action='store',
        dest='head_sha',
        required=True,
        metavar='Head_sha',
        help='Head Token, used for compare command'
    )

    # 解析代码检测结果 XML 文件
    parse_xml_result_args = subparsers.add_parser(
        'parse-result',
        help='parse code style result from XML'
    )
    parse_xml_result_args.add_argument(
        '--type',
        dest="type",
        type=str,
        default="parse_xml_result",
        help='Parse code style result from XML')
    parse_xml_result_args.add_argument(
        '--base-sha',
        action='store',
        dest='base_sha',
        required=True,
        metavar='Base_sha',
        help='Base Token, used for compare command'
    )
    parse_xml_result_args.add_argument(
        '--file',
        action='store',
        dest='fileName',
        required=True,
        metavar='The XML file name that used to parse code style result',
        help='The XML file name that used to parse code style result'
    )

    # 生成 review comment
    create_review_comments_args = subparsers.add_parser(
        'pr-comment',
        help='Create review comment for PR'
    )
    create_review_comments_args.add_argument(
        '--type',
        dest="type",
        type=str,
        default="create_review_comments",
        help='Create review comment for PR')
    add_common_args(create_review_comments_args)
    create_review_comments_args.add_argument(
        '--file',
        action='store',
        dest='fileName',
        required=True,
        metavar='The XML file name that used to parse code style result',
        help='The XML file name that used to parse code style result'
    )

    # create PR status
    create_pr_status_args = subparsers.add_parser(
        'pr-status',
        help='Create PR status'
    )
    create_pr_status_args.add_argument(
        '--type',
        dest="type",
        type=str,
        default="create_pr_status",
        help='Create PR status')
    create_pr_status_args.add_argument(
        '-t',
        '--token',
        action='store',
        dest='github_token',
        required=True,
        metavar='Github_Token',
        help='Github Token'
    )
    create_pr_status_args.add_argument(
        '--state-url',
        action='store',
        dest='status_url',
        required=True,
        metavar='PR status URL',
        help='PR status URL'
    )
    create_pr_status_args.add_argument(
        '--state',
        action='store',
        dest='state',
        required=True,
        metavar='PR status state',
        help='PR status state'
    )
    create_pr_status_args.add_argument(
        '--target_url',
        action='store',
        dest='target_url',
        required=True,
        metavar='PR status target_url',
        help='PR status target_url'
    )
    create_pr_status_args.add_argument(
        '--description',
        action='store',
        dest='description',
        required=True,
        metavar='PR status description',
        help='PR status description'
    )
    create_pr_status_args.add_argument(
        '--context',
        action='store',
        dest='context',
        required=True,
        metavar='PR status context',
        help='PR status context'
    )

    args = parser.parse_args()
    try:
        {
            'get_pr_info': get_pr_info,
            'parse_xml_result': parse_xml_result,
            'generate_pr_patch': generate_pr_patch,
            'create_review_comments': create_review_comments,
            'create_pr_status': create_pr_status,
        }[args.type](args)
    except KeyError:
        print(parser.print_help())
        sys.exit(-1)
    # request 异常退出
    except requests.HTTPError as e:
        print(e.message)
        sys.exit(e.response.status_code)
    except Exception as e:
        print(e.message)
        sys.exit(-1)


# 入口文件
get_args()
