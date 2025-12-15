import os
import sys
import traceback

import requests

# from qcloud_cos import CosConfig, CosS3Client
# from qcloud_cos.cos_exception import CosClientError, CosServiceError

# from config.paths import BASE_DIR
BASE_DIR = os.path.dirname(os.path.realpath(sys.argv[0]))

class OSSUploader:
    """对象存储文件上传工具类"""

    def __init__(self, env):
        api = {
            "test": "https://testlingqi.redbearai.com/api/user/file/common/upload/v2/anon",
            "prod": "https://lingqi.redbearai.com/api/user/file/common/upload/v2/anon"
        }
        self.api = api.get(env, "https://testlingqi.redbearai.com/api/user/file/common/upload/v2/anon")
        self.privacy = "false"
        self.headers = {
            "User-Agent": 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                          'AppleWebKit/537.36 (KHTML, like Gecko)'
                          ' Chrome/133.0.6833.84 Safari/537.36'
        }

    @staticmethod
    def _generate_object_key(file_path, prefix='xhs_'):
        """
        生成对象存储的Key

        :param file_path: 本地文件路径
        :param prefix: 存储前缀，用于分类存储
        :return: 生成的对象Key
        """
        # 文件md5值.后缀名
        filename = os.path.basename(file_path)
        filename = f"{filename}"

        # 组合成完整的对象Key
        return f"{prefix}{filename}"

    def upload_image(self, file_name, prefix='jd_'):
        """
        上传文件到COS并返回可访问的URL

        :param file_url: 文件路径
        :param file_name: 文件名称
        :param media_type: 文件类型
        :param prefix: 存储前缀，用于分类存储
        :return: 文件访问URL
        """
        # 检查文件是否存在



        file_path = os.path.join(BASE_DIR, file_name)

        # response = requests.get(url, headers=self.headers, stream=True)

        # if response.status_code == 200:
        #     with open(file_path, "wb") as f:
        #         for chunk in response.iter_content(1024):  # 分块写入，避免内存占用过大
        #             f.write(chunk)
        # else:
        #     raise Exception(f"文件下载失败,{file_name}")

        # 生成对象Key
        object_key = self._generate_object_key(file_path, prefix +file_name.split('.')[-1])

        try:
            upload_response = requests.post(
                self.api,
                data={
                    "privacy": self.privacy,
                    "fileName": object_key,
                }
            )

            if upload_response.status_code != 200:
                raise Exception('上传接口请求失败')
            resp = upload_response.json()
            name = resp["data"]["name"]
            file_url = resp["data"]["path"]
            policy = resp["data"]["policy"]
            with open(file_path, 'rb') as f:
                oss_push_resp = requests.post(
                    policy["host"],
                    files={
                        "key": policy["dir"],
                        "OSSAccessKeyId": policy["accessid"],
                        "name": name,
                        "policy": policy["policy"],
                        "success_action_status": 200,
                        "signature": policy["signature"],
                        "file": f,
                    }
                )
                if oss_push_resp.status_code == 200:
                    return file_url
            raise Exception("OSS上传失败")
        except Exception:
            raise Exception(f"上传失败: \n{traceback.format_exc()}")
        finally:
            print('success')
            # os.remove(file_path)


if __name__ == '__main__':
    cos_uploader = OSSUploader("prod")
    url =cos_uploader.upload_image('./example01.jpg')
    print(url)
