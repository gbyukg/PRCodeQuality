#!/usr/bin/env groovy

import groovy.json.JsonSlurper

public class codeCheckException extends hudson.AbortException {
    public codeCheckException() {
        super();
    }

    public codeCheckException(String message) {
        super(message);
    }
}

/**
 * Map params: 执行脚本时传递给脚本的参数将通过该参数设置
 * 执行 shell 时设置返回shell的输出还是shell的返回值
 */
def github_api(Map params, String returnType = 'getStdout')
{
    String return_val;
        withCredentials([string(credentialsId: CERDENTIALS_FOR_GITHUB_TOKEN, variable: 'github_token')]) {
            def cmd = null;
            switch(params.method) {
                // 获取 Pull Request 信息
                case 'pr':
                    withEnv(
                        [
                            "GITHUB_API_URL=env.GITHUB_API_URL",
                            "GITHUB_REPO_USER=env.GITHUB_REPO_USER",
                            "GITHUB_REPO_NAME=env.GITHUB_REPO_NAME",
                            "TMP_DIR=env.TMP_DIR"
                        ]
                    ) {
                        cmd = "python ${env.UTIL_SRC}" \
                        + " pr -p ${params.pr_number}" \
                        + " -t ${github_token}"
                    }
                    break
                // 生成 PR 补丁文件
                case 'patch':
                    withEnv(
                        [
                            "GITHUB_API_URL=env.GITHUB_API_URL",
                            "GITHUB_REPO_USER=env.GITHUB_REPO_USER",
                            "GITHUB_REPO_NAME=env.GITHUB_REPO_NAME",
                            "TMP_DIR=env.TMP_DIR"
                        ]
                    ) {
                        cmd = "python ${env.UTIL_SRC} patch" \
                        + " --base-sha ${params.base_sha} " \
                        + "--head-sha ${params.head_sha} " \
                        + "-t ${github_token}"
                    }
                    break
                // 解析 xml 文件
                case 'parse_xml_result':
                    withEnv(
                        [
                            "GITHUB_API_URL=env.GITHUB_API_URL",
                            "GITHUB_REPO_USER=env.GITHUB_REPO_USER",
                            "GITHUB_REPO_NAME=env.GITHUB_REPO_NAME",
                            "GITHUB_REPO_DIR=env.GITHUB_REPO_DIR"
                        ]
                    ) {
                        cmd = "python ${env.UTIL_SRC} parse-result --base-sha ${params.base_sha} --file ${params.resultFile}"
                    }
                    break
                // 创建/更新 PR status
                case 'pr_status':
                    cmd = "python ${env.UTIL_SRC} " \
                    + "pr-status " \
                    + "-t ${params.github_token} " \
                    + "--state-url \"${params.state_url}\" " \
                    + "--state ${params.state} " \
                    + "--target_url \"${params.target_url}\" " \
                    + "--description \"${params.description}\" " \
                    + "--context \"${params.context}\""
                    break
                // 直接执行脚本将导致失败
                case 'pr_comment':
                    withEnv(
                        [
                            "GITHUB_API_URL=env.GITHUB_API_URL",
                            "GITHUB_REPO_USER=env.GITHUB_REPO_USER",
                            "GITHUB_REPO_NAME=env.GITHUB_REPO_NAME",
                            "GITHUB_REPO_DIR=env.GITHUB_REPO_DIR",
                            "TMP_DIR=env.TMP_DIR"
                        ]
                    ) {
                        cmd = "python ${env.UTIL_SRC} " \
                        + "pr-comment " \
                        + "-p ${params.pr_number} -t ${params.github_token} " \
                        + "--file ${params.checkResultFile}"
                    }
                    break
                default:
                    cmd = "python ${env.UTIL_SRC}"
            }
            try {
                if (returnType == 'getStdout') {
                    return_val =  sh(
                        returnStdout: true,
                        script: cmd
                    ).trim();
                } else {
                    return_val =  sh(
                        returnStatus: true,
                        script: cmd
                    );
                }
            } catch (hudson.AbortException e) {
                error "Command failed\n${cmd}\n" + e.getMessage()
            }
        };
    return return_val;
}

node ('master')
{
    try {
        env.PATH = "/usr/local/php5.6.30/bin:/usr/local/node-v6.10.3-linux-x64/bin:${env.PATH}"

        stage('Getting PR info...') {
            pwd
            def return_val = null;

            // 获取 pull request 信息
            //     script pr -p pr_number -t token
            // 脚本将返回json格式的字符串
            return_val = github_api([method: 'pr', pr_number:params.PR_NUMBER])

            def jsonSlurper = new JsonSlurper();
            def pr_info = jsonSlurper.parseText(return_val);

            // 检测已经被merge的分之没有意义
            if (pr_info.merged) {
                jsonSlurper = null;
                pr_info = null;
                def msg = "The PR [${PR_NUMBER} https://github.com/sugareps/Mango/pull/${PR_NUMBER}] has been merged, can't check code style anymore.";
                send_msg_slack("danger", msg);
                error msg;
            }

            // 将获取的 PR 信息设置为环境变量
            env.PR_NUMBER       = pr_info.number;
            env.PR_TITLE        = pr_info.title;
            env.PR_URL          = pr_info.url;
            env.PR_STATE        = pr_info.state;
            env.PR_BASE_SHA     = pr_info.base.sha;
            env.PR_BASE_REF     = pr_info.base.ref;
            env.PR_BASE_LABEL   = pr_info.head.label;
            env.PR_HEAD_SHA     = pr_info.head.sha;
            env.PR_HEAD_REF     = pr_info.head.ref;
            env.PR_HEAD_LABEL   = pr_info.head.label;
            env.PR_statuses_url = pr_info.statuses_url;
        }

        stage('create PR status') {
            // 设置 PR status 为 pending
            return_val = github_api(
                [
                    method: 'pr_status',
                    pr_number:params.PR_NUMBER,
                    github_token: 'cf424794d4c561b76faa189326b4f7077569de5e',
                    state_url: env.PR_statuses_url,
                    state: 'pending',
                    target_url: "${env.BUILD_URL}/console",
                    description: 'Checking code standard',
                    context: 'China CI',
                ]
            )
        }

        stage('Generating patch file...') {
            pwd
            // 通过 GitHub API 获取 PR diff 文件
            // 脚本需要 TMP 环境变量, 将生成的diff文件保存到该环境变量指定的目录中
            //     script patch -t github_token --base-sha base_sha --head-sha head_sha
            def return_val = github_api(
                [
                    method: 'patch',
                    base_sha:env.PR_BASE_SHA,
                    head_sha:env.PR_HEAD_SHA
                ]
            );
            echo return_val
            env.PATCHFILELIST = return_val
        }

        stage('Fetch Sugar code...') {
            pwd
            // Checkout branch
            checkout(
                [
                    $class: 'GitSCM',
                    branches: [
                        [name: "sugareps/pr/${env.PR_NUMBER}"]
                    ],
                    doGenerateSubmoduleConfigurations: false,
                    extensions: [
                        [
                            $class: 'CloneOption',
                            depth: 50,
                            noTags: false,
                            reference: '',
                            shallow: false,
                            timeout: 20
                        ],
                        [
                            $class: 'CleanBeforeCheckout'
                        ],
                        [
                            $class: 'RelativeTargetDirectory',
                            relativeTargetDir: "${env.GITHUB_REPO_NAME}"
                        ],
                        [
                            $class: 'SubmoduleOption',
                            disableSubmodules: false,
                            parentCredentials: true,
                            recursiveSubmodules: true,
                            reference: '',
                            trackingSubmodules: false
                        ],
                        [
                            $class: 'DisableRemotePoll'
                        ]
                    ],
                    submoduleCfg: [],
                    userRemoteConfigs: [
                        [
                            credentialsId: 'github_ssh_key',
                            name: 'origin',
                            refspec: '+refs/pull/*/head:refs/remotes/sugareps/pr/* +refs/heads/*:refs/remotes/origin/*',
                            url: env.GITHUB_REPO_URL
                        ]
                    ]
                ]
            );
        }

        stage('Checking code style...') {
            pwd
            def php_dir = tool name: 'php', type: 'com.cloudbees.jenkins.plugins.customtools.CustomTool'
            def php = "${php_dir}/bin/php"
            def checkCMD = env.CODECHECKCMD // 通过 Jenkins Job 设定的环境变量
            def checkStandard = env.CODECHECKSTANDARD
            def fileList = env.PATCHFILELIST
            def codeCheckResultFile = env.CODECHECKRESULTFILE
            def checkStatus = 0

            // 进入到 Mango 目录下
            dir("${env.GITHUB_REPO_DIR}") {
                // 执行代码检测命令: PHPCS
                // 即使返回值为1, 有错误被检测到
                checkStatus = sh returnStatus: true, script: "${php} ${checkCMD} \
                --standard=${checkStandard} \
                --report=checkstyle \
                --report-file=${codeCheckResultFile} \
                ${fileList}"
            }
        }

        stage('Parsing code check result...') {
            // 解析生成的 xml 文件
            // 并移除掉不在本次 PR 中产生的错误信息
            // 返回值为找到的错误数量
            def return_val = github_api(
                [
                    method: 'parse_xml_result',
                    base_sha: env.PR_BASE_SHA,
                    pr_number: params.PR_NUMBER,
                    resultFile: "${env.GITHUB_REPO_DIR}/${env.CODECHECKRESULTFILE}",
                ],
                'getStatus'
            );
            env.ERROR_COUNT = return_val
            echo "Totally error count: ${env.ERROR_COUNT}"
        }

        // 创建 review comment
        stage('Uploading PR Review comment') {
            return_val = github_api(
                [
                    method: 'pr_comment',
                    pr_number: params.PR_NUMBER,
                    github_token: 'cf424794d4c561b76faa189326b4f7077569de5e',
                    checkResultFile: "${env.GITHUB_REPO_NAME}/${env.CODECHECKRESULTFILE}"
                ]
            )
        }

        stage('Uploading result') {
            // 将结果上传到 Jenkins 中
            def result = checkstyle(
                canComputeNew: false,
                canRunOnFailed: true,
                defaultEncoding: '',
                failedTotalAll: '1',
                healthy: '0',
                pattern: "${env.GITHUB_REPO_NAME}/${env.CODECHECKRESULTFILE}",
                unHealthy: '0',
                unstableTotalAll: '0'
            )

            // 创建 review comment
            return_val = github_api(
                [
                    method: 'pr_comment',
                    pr_number: params.PR_NUMBER,
                    github_token: 'cf424794d4c561b76faa189326b4f7077569de5e',
                    checkResultFile: "${env.GITHUB_REPO_NAME}/${env.CODECHECKRESULTFILE}"
                ]
            )

            // 说明检测成功
            if (env.ERROR_COUNT == '0') {
                github_api(
                    [
                        method: 'pr_status',
                        github_token: 'cf424794d4c561b76faa189326b4f7077569de5e',
                        state_url: env.PR_statuses_url,
                        state: 'success',
                        target_url: "${env.JOB_URL}",
                        description: 'All check passed',
                        context: 'China CI',
                    ]
                )
            } else {
                // 抛出代码检测异常
                throw new codeCheckException('Code does not meet coding standard')
            }
        }
    } catch (codeCheckException e) {
        echo '此次 PR 中修改的代码不符合标准'
        github_api(
            [
                method: 'pr_status',
                github_token: 'cf424794d4c561b76faa189326b4f7077569de5e',
                state_url: env.PR_statuses_url,
                state: 'error',
                target_url: "${env.BUILD_URL}/checkstyleResult",
                description: e.getMessage(),
                context: 'China CI',
            ]
        )
        currentBuild.result = 'FAILURE';
    } catch (hudson.AbortException e) {
        // 设置 PR 状态为失败
        github_api(
            [
                method: 'pr_status',
                github_token: 'cf424794d4c561b76faa189326b4f7077569de5e',
                state_url: env.PR_statuses_url,
                state: 'error',
                target_url: "${env.BUILD_URL}/console",
                description: 'Code quality job failed',
                context: 'China CI',
            ]
        )
        currentBuild.result = 'FAILURE';
    }
}
