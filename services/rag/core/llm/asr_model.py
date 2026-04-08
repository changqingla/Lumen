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

import json
import os
import re
import tempfile
from abc import ABC
from http import HTTPStatus
from typing import Dict, Any, List
from urllib import request

import requests
from core.utils import num_tokens_from_string


class Base(ABC):
    def __init__(self, key, model_name, base_url="", **kwargs):
        """
        Abstract base class constructor for ASR models.
        Parameters are not stored; subclasses should handle their own initialization.
        """
        pass

    def asr(self, audio_file_path: str, **kwargs) -> Dict[str, Any]:
        """
        Abstract method for audio speech recognition.
        
        Args:
            audio_file_path: Path to the audio file
            **kwargs: Additional parameters
            
        Returns:
            Dict containing transcription results
        """
        pass

    def normalize_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize the ASR result to a common format.
        
        Returns:
            {
                "text": "transcribed text",
                "language": "detected language",
                "confidence": 0.95,
                "segments": [...],  # Optional detailed segments
                "metadata": {...}   # Additional metadata
            }
        """
        return result


class QwenASR(Base):
    _FACTORY_NAME = "Tongyi-Qianwen"

    def __init__(self, key, model_name="paraformer-v2", base_url="", **kwargs):
        import dashscope
        
        self.model_name = model_name
        dashscope.api_key = key
        self.language_hints = kwargs.get("language_hints", ["zh", "en"])

    def asr(self, audio_file_path: str, **kwargs) -> Dict[str, Any]:
        """
        Perform ASR using Qwen/Dashscope API
        
        Supports three models:
        - paraformer-v2: General ASR with language hints support
        - sensevoice-v1: Rich text ASR with emotion and event detection
        - fun-asr: Fast ASR model
        """
        from dashscope.audio.asr import Transcription
        
        try:
            # Upload file and get URL (simplified - in real implementation you'd upload to OSS)
            # For now, assume the audio_file_path is already a URL or we need to upload it
            if audio_file_path.startswith(('http://', 'https://')):
                file_urls = [audio_file_path]
            else:
                # In real implementation, you would upload the local file to OSS and get URL
                raise ValueError("Local file upload to OSS not implemented. Please provide a URL.")

            # Prepare parameters based on model
            call_params = {
                'model': self.model_name,
                'file_urls': file_urls
            }
            
            # Add language hints for paraformer-v2
            if self.model_name == 'paraformer-v2':
                call_params['language_hints'] = self.language_hints

            # Make async call
            task_response = Transcription.async_call(**call_params)
            
            if not task_response or not hasattr(task_response, 'output'):
                raise RuntimeError("Failed to submit ASR task")

            # Wait for completion
            transcribe_response = Transcription.wait(task=task_response.output.task_id)
            
            if transcribe_response.status_code != HTTPStatus.OK:
                raise RuntimeError(f"ASR failed: {transcribe_response.output.message}")

            # Process results based on model type
            if self.model_name == 'sensevoice-v1':
                return self._process_sensevoice_result(transcribe_response.output)
            else:
                return self._process_standard_result(transcribe_response.output)

        except Exception as e:
            raise RuntimeError(f"**ERROR**: {str(e)}")

    def _process_standard_result(self, output: Dict[str, Any]) -> Dict[str, Any]:
        """Process standard ASR results (paraformer-v2, fun-asr)"""
        results = output.get('results', [])
        if not results:
            return {"text": "", "confidence": 0.0, "language": "unknown"}

        # Combine all transcriptions
        all_text = []
        total_confidence = 0.0
        segments = []
        
        for result in results:
            if result.get('subtask_status') == 'SUCCEEDED':
                transcription_url = result.get('transcription_url')
                if transcription_url:
                    # Fetch detailed results
                    detailed_result = json.loads(request.urlopen(transcription_url).read().decode('utf8'))
                    
                    for transcript in detailed_result.get('transcripts', []):
                        text = transcript.get('text', '')
                        all_text.append(text)
                        
                        # Extract segments if available
                        for sentence in transcript.get('sentences', []):
                            segments.append({
                                'text': sentence.get('text', ''),
                                'start_time': sentence.get('begin_time', 0),
                                'end_time': sentence.get('end_time', 0),
                                'confidence': sentence.get('confidence', 1.0)
                            })
                            total_confidence += sentence.get('confidence', 1.0)

        combined_text = ' '.join(all_text)
        avg_confidence = total_confidence / len(segments) if segments else 1.0
        
        return self.normalize_result({
            "text": combined_text,
            "confidence": avg_confidence,
            "language": "auto-detected",
            "segments": segments,
            "metadata": {
                "model": self.model_name,
                "token_count": num_tokens_from_string(combined_text)
            }
        })

    def _process_sensevoice_result(self, output: Dict[str, Any]) -> Dict[str, Any]:
        """Process SenseVoice results with emotion and event detection"""
        results = output.get('results', [])
        if not results:
            return {"text": "", "confidence": 0.0, "language": "unknown"}

        all_text = []
        segments = []
        emotions = []
        events = []

        for result in results:
            if result.get('subtask_status') == 'SUCCEEDED':
                transcription_url = result.get('transcription_url')
                if transcription_url:
                    detailed_result = json.loads(request.urlopen(transcription_url).read().decode('utf8'))
                    parsed_result = self._parse_sensevoice_result(detailed_result)
                    
                    for transcript in parsed_result.get('transcripts', []):
                        text = transcript.get('text', '')
                        all_text.append(text)
                        
                        for sentence in transcript.get('sentences', []):
                            segment = {
                                'text': sentence.get('text', ''),
                                'start_time': sentence.get('begin_time', 0),
                                'end_time': sentence.get('end_time', 0),
                                'confidence': sentence.get('confidence', 1.0)
                            }
                            
                            # Add emotion and event info
                            if 'emotion' in sentence:
                                segment['emotion'] = sentence['emotion']
                                emotions.extend(sentence['emotion'])
                            
                            if 'event' in sentence:
                                segment['event'] = sentence['event']
                                events.extend(sentence['event'])
                            
                            segments.append(segment)

        combined_text = ' '.join(all_text)
        
        return self.normalize_result({
            "text": combined_text,
            "confidence": 1.0,  # SenseVoice doesn't provide confidence scores
            "language": "auto-detected",
            "segments": segments,
            "emotions": list(set(emotions)) if emotions else [],
            "events": list(set(events)) if events else [],
            "metadata": {
                "model": self.model_name,
                "token_count": num_tokens_from_string(combined_text)
            }
        })

    def _parse_sensevoice_result(self, data: Dict[str, Any], keep_trans=True, keep_emotions=True, keep_events=True) -> Dict[str, Any]:
        """
        Parse SenseVoice recognition results
        """
        emotion_list = ['NEUTRAL', 'HAPPY', 'ANGRY', 'SAD']
        event_list = ['Speech', 'Applause', 'BGM', 'Laughter']
        
        all_tags = ['Speech', 'Applause', 'BGM', 'Laughter',
                    'NEUTRAL', 'HAPPY', 'ANGRY', 'SAD', 'SPECIAL_TOKEN_1']
        tags_to_cleanup = []
        for tag in all_tags:
            tags_to_cleanup.extend([f'<|{tag}|> ', f'<|/{tag}|>', f'<|{tag}|>'])

        def get_clean_text(text: str):
            for tag in tags_to_cleanup:
                text = text.replace(tag, '')
            pattern = r"\s{2,}"
            text = re.sub(pattern, " ", text).strip()
            return text

        for item in data['transcripts']:
            for sentence in item['sentences']:
                if keep_emotions:
                    emotions_pattern = r'<\|(' + '|'.join(emotion_list) + r')\|>'
                    emotions = re.findall(emotions_pattern, sentence['text'])
                    sentence['emotion'] = list(set(emotions))
                    if not sentence['emotion']:
                        sentence.pop('emotion', None)

                if keep_events:
                    events_pattern = r'<\|(' + '|'.join(event_list) + r')\|>'
                    events = re.findall(events_pattern, sentence['text'])
                    sentence['event'] = list(set(events))
                    if not sentence['event']:
                        sentence.pop('event', None)

                if keep_trans:
                    sentence['text'] = get_clean_text(sentence['text'])
                else:
                    sentence.pop('text', None)

            if keep_trans:
                item['text'] = get_clean_text(item['text'])
            else:
                item.pop('text', None)
            
            item['sentences'] = list(filter(lambda x: 'text' in x or 'emotion' in x or 'event' in x, item['sentences']))
        
        return data


class SiliconFlowASR(Base):
    _FACTORY_NAME = "SILICONFLOW"

    def __init__(self, key, model_name="FunAudioLLM/SenseVoiceSmall", base_url="https://api.siliconflow.cn/v1", **kwargs):
        if not base_url:
            base_url = "https://api.siliconflow.cn/v1"
        
        self.api_key = key
        self.model_name = model_name
        self.base_url = base_url
        self.headers = {
            "Authorization": f"Bearer {self.api_key}"
        }

    def asr(self, audio_file_path: str, **kwargs) -> Dict[str, Any]:
        """
        Perform ASR using SiliconFlow API
        """
        try:
            # Prepare the multipart form data
            if not os.path.exists(audio_file_path):
                raise FileNotFoundError(f"Audio file not found: {audio_file_path}")

            with open(audio_file_path, 'rb') as audio_file:
                files = {
                    'file': (os.path.basename(audio_file_path), audio_file, 'audio/wav'),
                    'model': (None, self.model_name)
                }
                
                response = requests.post(
                    f"{self.base_url}/audio/transcriptions",
                    headers=self.headers,
                    files=files,
                    timeout=60
                )

            if response.status_code != 200:
                raise RuntimeError(f"API request failed: {response.status_code}, {response.text}")

            result = response.json()
            transcribed_text = result.get("text", "")

            return self.normalize_result({
                "text": transcribed_text,
                "confidence": 1.0,  # SiliconFlow API doesn't provide confidence scores
                "language": "auto-detected",
                "segments": [{
                    "text": transcribed_text,
                    "start_time": 0,
                    "end_time": 0,
                    "confidence": 1.0
                }],
                "metadata": {
                    "model": self.model_name,
                    "token_count": num_tokens_from_string(transcribed_text)
                }
            })

        except Exception as e:
            raise RuntimeError(f"**ERROR**: {str(e)}")


class LocalASR(Base):
    _FACTORY_NAME = "Local-ASR"

    def __init__(self, key, model_name="fun-asr", base_url="http://localhost:8000", **kwargs):
        """
        Local ASR model with FunASR-compatible interface
        """
        if not base_url:
            base_url = "http://localhost:8000"
        
        self.model_name = model_name
        self.base_url = base_url.rstrip('/')
        self.headers = {
            "Content-Type": "application/json"
        }
        # Local model might not need authentication, but keep it flexible
        if key and key != "local":
            self.headers["Authorization"] = f"Bearer {key}"

    def asr(self, audio_file_path: str, **kwargs) -> Dict[str, Any]:
        """
        Perform ASR using local FunASR-compatible API
        Supports both OpenAI-style multipart and custom JSON API
        """
        try:
            # Support URL inputs by downloading to a temporary file
            temp_path = None
            local_path = audio_file_path
            if isinstance(audio_file_path, str) and audio_file_path.startswith(('http://', 'https://')):
                resp = requests.get(audio_file_path, timeout=60)
                if resp.status_code != 200:
                    raise RuntimeError(f"Failed to download audio URL: {audio_file_path}, status={resp.status_code}")
                # Infer file extension from URL path
                url_path = audio_file_path.split('?', 1)[0]
                suffix = os.path.splitext(url_path)[1] or '.wav'
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(resp.content)
                    temp_path = tmp.name
                local_path = temp_path
            elif isinstance(audio_file_path, str) and audio_file_path.startswith('file://'):
                local_path = audio_file_path.replace('file://', '', 1)

            if not os.path.exists(local_path):
                raise FileNotFoundError(f"Audio file not found: {audio_file_path}")

            # Try multipart form first (OpenAI Whisper style)
            try:
                with open(local_path, 'rb') as audio_file:
                    files = {
                        'file': (os.path.basename(local_path), audio_file, 'audio/wav'),
                        'model': (None, self.model_name)
                    }
                    
                    # Remove Content-Type for multipart
                    headers = {k: v for k, v in self.headers.items() if k != "Content-Type"}
                    
                    response = requests.post(
                        f"{self.base_url}/v1/audio/transcriptions",
                        headers=headers,
                        files=files,
                        timeout=60
                    )
                    
                    if response.status_code == 200:
                        return self._parse_response(response)
            except Exception as e:
                # Multipart failed, try other formats
                pass
            
            # Try custom JSON API (like your FunASR service)
            import base64
            with open(local_path, 'rb') as audio_file:
                audio_data = base64.b64encode(audio_file.read()).decode('utf-8')
            
            # Detect audio format
            file_ext = os.path.splitext(local_path)[1][1:]  # Remove the dot
            
            # Format 1: /v1/audio/recognition (your FunASR service style)
            try:
                # Support both base URL and full endpoint in base_url
                if self.base_url.endswith('/v1/audio/recognition'):
                    recognition_url = self.base_url
                else:
                    recognition_url = f"{self.base_url}/v1/audio/recognition"
                payload = {
                    "model": self.model_name,
                    "messages": [
                        {
                            "role": "system",
                            "content": "请转录这段音频。"
                        },
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "audio_url",
                                    "audio_url": {
                                        "url": f"data:audio/{file_ext};base64,{audio_data}"
                                    }
                                }
                            ]
                        }
                    ]
                }
                
                response = requests.post(
                    recognition_url,
                    headers=self.headers,
                    json=payload,
                    timeout=60
                )
                
                if response.status_code == 200:
                    result = self._parse_response(response)
                    if temp_path:
                        try:
                            os.remove(temp_path)
                        except Exception:
                            pass
                    return result
            except Exception as e:
                # Format 1 failed, try Format 2
                pass
            
            # Format 2: Simple /asr endpoint
            payload = {
                "model": self.model_name,
                "audio": audio_data,
                "format": file_ext
            }
            
            # Build /asr endpoint robustly
            if self.base_url.endswith('/asr'):
                asr_url = self.base_url
            elif self.base_url.endswith('/v1/audio/recognition'):
                asr_url = self.base_url.rsplit('/v1/audio/recognition', 1)[0] + '/asr'
            elif self.base_url.endswith('/v1/audio/transcriptions'):
                asr_url = self.base_url.rsplit('/v1/audio/transcriptions', 1)[0] + '/asr'
            else:
                asr_url = f"{self.base_url}/asr"

            response = requests.post(
                asr_url,
                headers=self.headers,
                json=payload,
                timeout=60
            )

            if response.status_code != 200:
                raise RuntimeError(f"Local ASR API request failed: {response.status_code}, {response.text}")

            result = response.json()
            
            # Handle different response formats
            if "text" in result:
                # Simple format like SiliconFlow
                transcribed_text = result["text"]
                segments = [{
                    "text": transcribed_text,
                    "start_time": 0,
                    "end_time": 0,
                    "confidence": result.get("confidence", 1.0)
                }]
            elif "transcripts" in result:
                # FunASR detailed format
                transcribed_text = ""
                segments = []
                for transcript in result["transcripts"]:
                    text = transcript.get("text", "")
                    transcribed_text += text + " "
                    
                    for sentence in transcript.get("sentences", []):
                        segments.append({
                            "text": sentence.get("text", ""),
                            "start_time": sentence.get("begin_time", 0),
                            "end_time": sentence.get("end_time", 0),
                            "confidence": sentence.get("confidence", 1.0)
                        })
                
                transcribed_text = transcribed_text.strip()
            else:
                raise RuntimeError(f"Unexpected response format: {result}")

            parsed = self.normalize_result({
                "text": transcribed_text,
                "confidence": result.get("confidence", 1.0),
                "language": result.get("language", "auto-detected"),
                "segments": segments,
                "metadata": {
                    "model": self.model_name,
                    "token_count": num_tokens_from_string(transcribed_text)
                }
            })

        except Exception as e:
            raise RuntimeError(f"**ERROR**: {str(e)}")
        finally:
            if 'temp_path' in locals() and temp_path:
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
    
    def _parse_response(self, response: requests.Response) -> Dict[str, Any]:
        """
        Parse response from various ASR API formats
        """
        result = response.json()
        
        # Format 1: OpenAI Whisper style - {"text": "..."}
        if "text" in result:
            transcribed_text = result["text"]
            return self.normalize_result({
                "text": transcribed_text,
                "confidence": result.get("confidence", 1.0),
                "language": result.get("language", "auto-detected"),
                "segments": [{
                    "text": transcribed_text,
                    "start_time": 0,
                    "end_time": 0,
                    "confidence": result.get("confidence", 1.0)
                }],
                "metadata": {
                    "model": self.model_name,
                    "token_count": num_tokens_from_string(transcribed_text)
                }
            })
        
        # Format 2: OpenAI Chat style - {"choices": [{"message": {"content": "..."}}]}
        elif "choices" in result and len(result["choices"]) > 0:
            choice = result["choices"][0]
            if "message" in choice:
                transcribed_text = choice["message"].get("content", "")
            elif "text" in choice:
                transcribed_text = choice["text"]
            else:
                transcribed_text = str(choice)
            
            return self.normalize_result({
                "text": transcribed_text,
                "confidence": 1.0,
                "language": "auto-detected",
                "segments": [{
                    "text": transcribed_text,
                    "start_time": 0,
                    "end_time": 0,
                    "confidence": 1.0
                }],
                "metadata": {
                    "model": self.model_name,
                    "token_count": num_tokens_from_string(transcribed_text)
                }
            })
        
        # Format 3: FunASR detailed format - {"transcripts": [...]}
        elif "transcripts" in result:
            transcribed_text = ""
            segments = []
            for transcript in result["transcripts"]:
                text = transcript.get("text", "")
                transcribed_text += text + " "
                
                for sentence in transcript.get("sentences", []):
                    segments.append({
                        "text": sentence.get("text", ""),
                        "start_time": sentence.get("begin_time", 0),
                        "end_time": sentence.get("end_time", 0),
                        "confidence": sentence.get("confidence", 1.0)
                    })
            
            transcribed_text = transcribed_text.strip()
            
            return self.normalize_result({
                "text": transcribed_text,
                "confidence": result.get("confidence", 1.0),
                "language": result.get("language", "auto-detected"),
                "segments": segments,
                "metadata": {
                    "model": self.model_name,
                    "token_count": num_tokens_from_string(transcribed_text)
                }
            })
        
        else:
            raise RuntimeError(f"Unexpected response format: {result}")


# Factory function to create ASR instances
def create_asr_model(factory_name: str, key: str, model_name: str, base_url: str = "", **kwargs) -> Base:
    """
    Factory function to create ASR model instances
    
    Args:
        factory_name: Name of the ASR provider
        key: API key or authentication token
        model_name: Name of the specific model to use
        base_url: Base URL for the API (optional)
        **kwargs: Additional parameters
        
    Returns:
        ASR model instance
    """
    factory_mapping = {
        "Tongyi-Qianwen": QwenASR,
        "SILICONFLOW": SiliconFlowASR,
        "Local-ASR": LocalASR,
    }
    
    if factory_name not in factory_mapping:
        raise ValueError(f"Unsupported ASR factory: {factory_name}. Supported: {list(factory_mapping.keys())}")
    
    asr_class = factory_mapping[factory_name]
    return asr_class(key=key, model_name=model_name, base_url=base_url, **kwargs)


# Utility function for batch processing
def batch_asr(factory_name: str, key: str, model_name: str, audio_files: List[str], 
              base_url: str = "", **kwargs) -> List[Dict[str, Any]]:
    """
    Process multiple audio files with ASR
    
    Args:
        factory_name: Name of the ASR provider
        key: API key or authentication token
        model_name: Name of the specific model to use
        audio_files: List of audio file paths
        base_url: Base URL for the API (optional)
        **kwargs: Additional parameters
        
    Returns:
        List of ASR results
    """
    asr_model = create_asr_model(factory_name, key, model_name, base_url, **kwargs)
    results = []
    
    for audio_file in audio_files:
        try:
            result = asr_model.asr(audio_file, **kwargs)
            result["file_path"] = audio_file
            results.append(result)
        except Exception as e:
            results.append({
                "file_path": audio_file,
                "error": str(e),
                "text": "",
                "confidence": 0.0
            })
    
    return results
