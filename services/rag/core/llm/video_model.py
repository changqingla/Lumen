#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

import os
import logging
import base64
import tempfile
import subprocess
from abc import ABC
from typing import Optional, Dict, Any, List

from openai import OpenAI
from core.utils import num_tokens_from_string, total_token_count_from_response, is_english

logger = logging.getLogger(__name__)


def get_video_info(video_path: str) -> Dict[str, Any]:
    """获取视频信息"""
    try:
        # 使用ffprobe获取视频信息
        cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json', 
            '-show_format', '-show_streams', video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        import json
        info = json.loads(result.stdout)
        
        # 提取视频流信息
        video_stream = None
        for stream in info.get('streams', []):
            if stream.get('codec_type') == 'video':
                video_stream = stream
                break
        
        if not video_stream:
            raise ValueError("No video stream found")
        
        format_info = info.get('format', {})
        
        return {
            'duration': float(format_info.get('duration', 0)),
            'size': int(format_info.get('size', 0)),
            'width': int(video_stream.get('width', 0)),
            'height': int(video_stream.get('height', 0)),
            'fps': eval(video_stream.get('r_frame_rate', '0/1')),  # 帧率
            'codec': video_stream.get('codec_name', ''),
            'bitrate': int(format_info.get('bit_rate', 0))
        }
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError) as e:
        logger.warning(f"Failed to get video info using ffprobe: {e}")
        # 回退到基本文件信息
        stat = os.stat(video_path)
        return {
            'duration': 0,
            'size': stat.st_size,
            'width': 0,
            'height': 0,
            'fps': 0,
            'codec': '',
            'bitrate': 0
        }


def video_to_base64(video_path: str, max_size_mb: float = 10.0) -> str:
    """
    将视频转换为base64编码
    
    Args:
        video_path: 视频文件路径
        max_size_mb: 最大文件大小限制（MB）
        
    Returns:
        base64编码的视频数据
        
    Raises:
        ValueError: 当视频文件超过大小限制时
        
    Note:
        OpenAI API对base64编码的视频有10MB限制，这是因为：
        1. HTTP请求大小限制：大多数HTTP服务器和代理对请求体大小有限制
        2. 内存使用：base64编码会增加约33%的数据大小，10MB视频编码后约13MB
        3. 处理效率：过大的视频会影响API响应时间和服务器资源
        4. 网络传输：大文件传输容易超时或失败
        5. 模型限制：视频理解模型对输入大小有处理限制
    """
    try:
        # 检查文件大小
        file_size = os.path.getsize(video_path)
        file_size_mb = file_size / (1024 * 1024)
        
        if file_size_mb > max_size_mb:
            raise ValueError(
                f"视频文件大小 {file_size_mb:.2f}MB 超过了 {max_size_mb}MB 的限制。"
                f"请使用更小的视频文件，或考虑降低视频分辨率、帧率或时长。"
                f"建议视频大小不超过 {max_size_mb}MB。"
            )
        
        # 读取并编码视频文件
        with open(video_path, 'rb') as video_file:
            video_data = video_file.read()
            base64_data = base64.b64encode(video_data).decode('utf-8')
            
        # 计算base64编码后的大小
        encoded_size_mb = len(base64_data) / (1024 * 1024)
        logger.info(f"Video encoded to base64: {encoded_size_mb:.2f}MB")
        
        if encoded_size_mb > max_size_mb:
            raise ValueError(
                f"视频文件经base64编码后大小 {encoded_size_mb:.2f}MB 超过了 {max_size_mb}MB 的限制。"
                f"原始文件: {file_size_mb:.2f}MB，编码后增加了约33%。"
                f"请使用更小的视频文件。"
            )
        
        return base64_data
        
    except Exception as e:
        if "超过了" in str(e):
            raise  # 重新抛出大小限制错误
        logger.error(f"Video to base64 conversion failed: {e}")
        raise Exception(f"视频编码失败: {str(e)}")


def extract_video_frames(video_path: str, fps: float = None, max_frames: int = 10) -> List[str]:
    """
    从视频中提取关键帧并转换为base64
    
    Args:
        video_path: 视频路径
        fps: 提取帧率（None时使用默认值1.0）
        max_frames: 最大帧数
        
    Returns:
        base64编码的图片帧列表
    """
    try:
        # 如果fps为None，使用默认值1.0
        if fps is None:
            fps = 1.0
        
        temp_dir = tempfile.mkdtemp()
        frames_pattern = os.path.join(temp_dir, "frame_%03d.jpg")
        
        # 使用ffmpeg提取帧
        cmd = [
            'ffmpeg', '-i', video_path,
            '-vf', f'fps={fps}',  # 设置提取帧率
            '-vframes', str(max_frames),  # 限制帧数
            '-q:v', '2',  # 高质量JPEG
            '-y',
            frames_pattern
        ]
        
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        # 读取生成的帧并转换为base64
        frames_base64 = []
        for i in range(1, max_frames + 1):
            frame_path = os.path.join(temp_dir, f"frame_{i:03d}.jpg")
            if os.path.exists(frame_path):
                with open(frame_path, 'rb') as f:
                    frame_data = base64.b64encode(f.read()).decode('utf-8')
                    frames_base64.append(f"data:image/jpeg;base64,{frame_data}")
        
        # 清理临时文件
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
        
        logger.info(f"Extracted {len(frames_base64)} frames from video")
        return frames_base64
        
    except subprocess.CalledProcessError as e:
        logger.error(f"Frame extraction failed: {e.stderr}")
        raise Exception(f"帧提取失败: {e.stderr}")
    except Exception as e:
        logger.error(f"Frame extraction error: {e}")
        raise Exception(f"帧提取错误: {str(e)}")


class Base(ABC):
    def __init__(self, **kwargs):
        # Configure retry parameters
        self.max_retries = kwargs.get("max_retries", int(os.environ.get("LLM_MAX_RETRIES", 5)))
        self.base_delay = kwargs.get("retry_interval", float(os.environ.get("LLM_BASE_DELAY", 2.0)))
        self.max_rounds = kwargs.get("max_rounds", 5)

    def describe(self, video_path: str, prompt: Optional[str] = None, fps: float = 1.0) -> tuple:
        """
        描述视频内容
        
        Args:
            video_path: 视频文件路径（本地路径或URL）
            prompt: 可选的自定义提示词，如果不提供则使用默认提示词
            fps: 视频抽帧频率，表示每秒抽取的帧数
            
        Returns:
            tuple: (描述文本, token数量)
        """
        raise NotImplementedError("Please implement describe method!")

    def _form_history(self, system: str, history: List[Dict], video_path: str, fps: float = 1.0):
        """构建对话历史，包含视频信息"""
        hist = []
        if system:
            hist.append({"role": "system", "content": system})
        
        for h in history:
            if h["role"] == "user" and video_path:
                # 为用户消息添加视频内容
                h["content"] = self._video_prompt(h["content"], video_path, fps)
                video_path = None  # 只在第一个用户消息中添加视频
            hist.append(h)
        return hist

    def _video_prompt(self, text: str, video_path: str, fps: float = 1.0):
        """构建包含视频的提示词"""
        # 子类需要实现具体的视频提示词格式
        return text

    def chat(self, system: str, history: List[Dict], gen_conf: Dict, video_path: str = None, fps: float = 1.0, **kwargs):
        """视频对话接口"""
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=self._form_history(system, history, video_path, fps)
            )
            return response.choices[0].message.content.strip(), response.usage.total_tokens
        except Exception as e:
            return "**ERROR**: " + str(e), 0

    def chat_streamly(self, system: str, history: List[Dict], gen_conf: Dict, video_path: str = None, fps: float = 1.0, **kwargs):
        """流式视频对话接口"""
        ans = ""
        tk_count = 0
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=self._form_history(system, history, video_path, fps),
                stream=True
            )
            for resp in response:
                if not resp.choices[0].delta.content:
                    continue
                delta = resp.choices[0].delta.content
                ans = delta
                if resp.choices[0].finish_reason == "length":
                    ans += "...\nFor the content length reason, it stopped, continue?" if is_english([ans]) else "······\n由于长度的原因，回答被截断了，要继续吗？"
                if resp.choices[0].finish_reason == "stop":
                    tk_count += resp.usage.total_tokens
                yield ans
        except Exception as e:
            yield ans + "\n**ERROR**: " + str(e)

        yield tk_count

    def prompt(self, video_path: str, fps: float = 1.0):
        """生成默认的视频分析提示词"""
        return [
            {
                "role": "user",
                "content": self._video_prompt(
                    "请详细描述这个视频的内容，包括场景、人物、动作、对话等关键信息。"
                    if hasattr(self, 'lang') and self.lang.lower() == "chinese"
                    else "Please describe the content of this video in detail, including scenes, characters, actions, dialogues and other key information.",
                    video_path,
                    fps
                )
            }
        ]


class QwenVideo(Base):
    _FACTORY_NAME = "Tongyi-Qianwen"

    def __init__(self, key: str, model_name: str = "qwen3-vl-plus", lang: str = "Chinese", **kwargs):
        import dashscope
        
        self.api_key = key
        self.model_name = model_name
        self.lang = lang
        dashscope.api_key = key
        
        # 支持新加坡地域
        if kwargs.get("use_singapore_region", False):
            dashscope.base_http_api_url = "https://dashscope-intl.aliyuncs.com/api/v1"
        
        super().__init__(**kwargs)

    def describe(self, video_path: str, prompt: Optional[str] = None, fps: float = 2.0) -> tuple:
        """使用通义千问进行视频描述"""
        from dashscope import MultiModalConversation
        
        try:
            # 确保视频路径格式正确
            if not video_path.startswith(('http://', 'https://', 'file://')):
                if os.path.exists(video_path):
                    video_path = f"file://{os.path.abspath(video_path)}"
                else:
                    raise FileNotFoundError(f"Video file not found: {video_path}")
            
            # 构建默认提示词
            if not prompt:
                prompt = (
                    "请详细描述这个视频的内容，包括场景、人物、动作、对话等关键信息。"
                    if self.lang.lower() == "chinese"
                    else "Please describe the content of this video in detail, including scenes, characters, actions, dialogues and other key information."
                )
            
            # 构建消息
            messages = [
                {
                    'role': 'user',
                    'content': [
                        {'video': video_path, "fps": fps},
                        {'text': prompt}
                    ]
                }
            ]
            
            # 调用API
            response = MultiModalConversation.call(
                api_key=self.api_key,
                model=self.model_name,
                messages=messages
            )
            
            if response.status_code == 200:
                content = response["output"]["choices"][0]["message"]["content"][0]["text"]
                token_count = response.get("usage", {}).get("total_tokens", num_tokens_from_string(content))
                return content.strip(), token_count
            else:
                raise Exception(f"API call failed: {response}")
                
        except Exception as e:
            logger.error(f"QwenVideo describe failed: {e}")
            raise Exception(f"**ERROR**: {str(e)}")

    def _video_prompt(self, text: str, video_path: str, fps: float = 2.0):
        """构建通义千问的视频提示词格式"""
        if not video_path.startswith(('http://', 'https://', 'file://')):
            if os.path.exists(video_path):
                video_path = f"file://{os.path.abspath(video_path)}"
        
        return [
            {'video': video_path, "fps": fps},
            {'text': text}
        ]


class SiliconFlowVideo(Base):
    _FACTORY_NAME = "SILICONFLOW"

    def __init__(self, key: str, model_name: str = "Pro/Qwen/Qwen2-VL-72B-Instruct", 
                 base_url: str = "https://api.siliconflow.cn/v1", lang: str = "Chinese", **kwargs):
        if not base_url:
            base_url = "https://api.siliconflow.cn/v1"
        
        self.client = OpenAI(api_key=key, base_url=base_url)
        self.model_name = model_name
        self.lang = lang
        super().__init__(**kwargs)

    def describe(self, video_path: str, prompt: Optional[str] = None, fps: float = 1.0) -> tuple:
        """使用SiliconFlow进行视频描述"""
        try:
            # 构建默认提示词
            if not prompt:
                prompt = (
                    "请详细描述这个视频的内容，包括场景、人物、动作、对话等关键信息。"
                    if self.lang.lower() == "chinese"
                    else "Please describe the content of this video in detail, including scenes, characters, actions, dialogues and other key information."
                )
            
            # 构建消息 - SiliconFlow使用OpenAI格式
            messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt
                        },
                        {
                            "type": "video",
                            "video": self._prepare_video_input(video_path, fps)
                        }
                    ]
                }
            ]
            
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages
            )
            
            content = response.choices[0].message.content.strip()
            token_count = total_token_count_from_response(response)
            
            return content, token_count
            
        except Exception as e:
            logger.error(f"SiliconFlowVideo describe failed: {e}")
            raise Exception(f"**ERROR**: {str(e)}")

    def _prepare_video_input(self, video_path: str, fps: float = 1.0, use_frames: bool = False) -> Dict[str, Any]:
        """
        准备视频输入格式
        
        Args:
            video_path: 视频路径
            fps: 帧率
            use_frames: 是否使用帧提取模式（当视频过大时）
        """
        if video_path.startswith(('http://', 'https://')):
            return {"url": video_path}
        else:
            if not os.path.exists(video_path):
                raise FileNotFoundError(f"Video file not found: {video_path}")
            
            # 检查视频大小
            video_info = get_video_info(video_path)
            file_size_mb = video_info['size'] / (1024 * 1024)
            
            if file_size_mb > 10.0 or use_frames:
                # 视频过大，使用帧提取模式
                logger.info(f"Video {file_size_mb:.2f}MB is too large, extracting frames instead")
                frames = extract_video_frames(video_path, fps=fps, max_frames=10)
                return {
                    "type": "frames",
                    "frames": frames,
                    "original_size_mb": file_size_mb
                }
            else:
                # 直接使用base64编码
                base64_data = video_to_base64(video_path, max_size_mb=10.0)
                return {
                    "type": "video",
                    "data": f"data:video/mp4;base64,{base64_data}",
                    "size_mb": file_size_mb
                }

    def _video_prompt(self, text: str, video_path: str, fps: float = 1.0):
        """构建SiliconFlow的视频提示词格式"""
        video_input = self._prepare_video_input(video_path, fps)
        
        content = [{"type": "text", "text": text}]
        
        if video_input["type"] == "video":
            # 直接使用视频
            content.append({
                "type": "video",
                "video": {"url": video_input["data"]}
            })
        elif video_input["type"] == "frames":
            # 使用提取的帧
            for i, frame in enumerate(video_input["frames"]):
                content.append({
                    "type": "image_url",
                    "image_url": {"url": frame}
                })
            # 添加说明文本
            content.append({
                "type": "text", 
                "text": f"[以上是从视频中提取的{len(video_input['frames'])}帧画面，请基于这些帧分析视频内容]"
            })
        
        return content


class VLLMVideo(Base):
    _FACTORY_NAME = "VLLM"

    def __init__(self, key: str, model_name: str, base_url: str = "", lang: str = "Chinese", **kwargs):
        if not base_url:
            raise ValueError("VLLM base_url cannot be None")
        
        if not base_url.endswith('/'):
            base_url += '/'
        base_url = base_url + "v1"
        self.client = OpenAI(api_key=key, base_url=base_url)
        self.model_name = model_name.split("___")[0]
        self.lang = lang
        super().__init__(**kwargs)

    def describe(self, video_path: str, prompt: Optional[str] = None, fps: float = 1.0) -> tuple:
        """使用VLLM进行视频描述"""
        try:
            # 构建默认提示词
            if not prompt:
                prompt = (
                    "请详细描述这个视频的内容，包括场景、人物、动作、对话等关键信息。"
                    if self.lang.lower() == "chinese"
                    else "Please describe the content of this video in detail, including scenes, characters, actions, dialogues and other key information."
                )
            
            # 构建消息 - VLLM使用OpenAI兼容格式
            messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt
                        },
                        {
                            "type": "video",
                            "video": self._prepare_video_input(video_path, fps)
                        }
                    ]
                }
            ]
            
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages
            )
            
            content = response.choices[0].message.content.strip()
            token_count = total_token_count_from_response(response)
            
            return content, token_count
            
        except Exception as e:
            logger.error(f"VLLMVideo describe failed: {e}")
            raise Exception(f"**ERROR**: {str(e)}")

    def _prepare_video_input(self, video_path: str, fps: float = 1.0, use_frames: bool = False) -> Dict[str, Any]:
        """准备VLLM的视频输入格式"""
        if video_path.startswith(('http://', 'https://')):
            return {"url": video_path}
        else:
            if not os.path.exists(video_path):
                raise FileNotFoundError(f"Video file not found: {video_path}")
            
            # 检查视频大小
            video_info = get_video_info(video_path)
            file_size_mb = video_info['size'] / (1024 * 1024)
            
            if file_size_mb > 10.0 or use_frames:
                # 视频过大，使用帧提取模式
                logger.info(f"Video {file_size_mb:.2f}MB is too large for VLLM, extracting frames instead")
                frames = extract_video_frames(video_path, fps=fps, max_frames=10)
                return {
                    "type": "frames",
                    "frames": frames,
                    "original_size_mb": file_size_mb
                }
            else:
                # 直接使用base64编码
                base64_data = video_to_base64(video_path, max_size_mb=10.0)
                return {
                    "type": "video",
                    "data": f"data:video/mp4;base64,{base64_data}",
                    "size_mb": file_size_mb
                }

    def _video_prompt(self, text: str, video_path: str, fps: float = 1.0):
        """构建VLLM的视频提示词格式"""
        video_input = self._prepare_video_input(video_path, fps)
        
        content = [{"type": "text", "text": text}]
        
        if video_input["type"] == "video":
            # 直接使用视频
            content.append({
                "type": "video",
                "video": {"url": video_input["data"]}
            })
        elif video_input["type"] == "frames":
            # 使用提取的帧
            for i, frame in enumerate(video_input["frames"]):
                content.append({
                    "type": "image_url",
                    "image_url": {"url": frame}
                })
            # 添加说明文本
            content.append({
                "type": "text", 
                "text": f"[以上是从视频中提取的{len(video_input['frames'])}帧画面，请基于这些帧分析视频内容]"
            })
        
        return content


# 工厂函数
def create_video_model(factory_name: str, key: str, model_name: str, base_url: str = "", **kwargs) -> Base:
    """
    创建视频模型实例的工厂函数
    
    Args:
        factory_name: 厂商名称 (Tongyi-Qianwen, SILICONFLOW, VLLM)
        key: API密钥
        model_name: 模型名称
        base_url: 基础URL（可选）
        **kwargs: 其他参数
        
    Returns:
        视频模型实例
    """
    factory_mapping = {
        "Tongyi-Qianwen": QwenVideo,
        "SILICONFLOW": SiliconFlowVideo,
        "VLLM": VLLMVideo,
    }
    
    if factory_name not in factory_mapping:
        raise ValueError(f"Unsupported video model factory: {factory_name}. Supported: {list(factory_mapping.keys())}")
    
    model_class = factory_mapping[factory_name]
    
    if factory_name == "Tongyi-Qianwen":
        return model_class(key=key, model_name=model_name, **kwargs)
    else:
        return model_class(key=key, model_name=model_name, base_url=base_url, **kwargs)


# 批量处理函数
def batch_video_describe(factory_name: str, key: str, model_name: str, video_files: List[str], 
                        base_url: str = "", fps: float = 1.0, **kwargs) -> List[Dict[str, Any]]:
    """
    批量处理视频描述
    
    Args:
        factory_name: 厂商名称
        key: API密钥
        model_name: 模型名称
        video_files: 视频文件路径列表
        base_url: 基础URL（可选）
        fps: 抽帧频率
        **kwargs: 其他参数
        
    Returns:
        处理结果列表
    """
    video_model = create_video_model(factory_name, key, model_name, base_url, **kwargs)
    results = []
    
    for video_file in video_files:
        try:
            description, token_count = video_model.describe(video_file, fps=fps)
            results.append({
                "video_path": video_file,
                "description": description,
                "token_count": token_count,
                "success": True,
                "error": None
            })
        except Exception as e:
            results.append({
                "video_path": video_file,
                "description": "",
                "token_count": 0,
                "success": False,
                "error": str(e)
            })
    
    return results
