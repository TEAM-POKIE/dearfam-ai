import os
import logging
import json
import re
import uuid
import aiohttp
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from openai import AsyncOpenAI
from .s3_util import upload_video_to_s3, download_and_upload_image_to_s3, upload_image_to_s3

# 환경 변수 로드
load_dotenv()

# OpenAI 비동기 클라이언트
openai_api_key = os.getenv("CHAT_GPT_API_KEY")
if not openai_api_key:
    logging.warning("CHAT_GPT_API_KEY 환경 변수가 설정되지 않았습니다. 일부 기능이 제한될 수 있습니다.")
    openai_client = None
else:
    openai_client = AsyncOpenAI(api_key=openai_api_key)

modelslab_api_key = os.getenv("MODELSLAB_API_KEY")
if not modelslab_api_key:
    logging.warning("MODELSLAB_API_KEY 환경 변수가 설정되지 않았습니다. 일부 기능이 제한될 수 있습니다.")
    modelslab_api_key = None


class DiaryAIService:
    """일기 생성 AI 서비스"""
    
    @staticmethod
    async def generate_diary(text: str):
        """텍스트를 바탕으로 일기와 이미지를 생성"""
        if not openai_client:
            return {
                "title": "API 키 미설정",
                "content": "OpenAI API 키가 설정되지 않았습니다.",
                "image_url": ""
            }
        
        try:
            # OpenAI Chat API로 일기 생성
            response = await openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[{
                    "role": "user",
                    "content": f"""
                    {text}
                    내용을 바탕으로 초등학생 그림일기를 작성해줘. ~했다. 식으로 적어줘

                    예시처럼 **JSON만** 응답해줘. (맨 앞에 json, 설명, 코드블록, 마크다운, 줄바꿈 등 아무것도 붙이지 마!)
                    {{
                        "title": "제목 (15자 이내)",
                        "content": "일기 내용 (최소 100자 이상, 최대 150자 이내)"
                    }}
                    """
                }],
                temperature=0.7,
                max_tokens=300
            )
            diary_text = response.choices[0].message.content
            try:
                # json 형식으로 파싱 후 저장
                cleaned = re.sub(r"^```json|```$", "", diary_text).strip()
                diary_dict = json.loads(cleaned)
            except Exception as parse_error:
                raise ValueError(f"OpenAI JSON 파싱 실패: {parse_error}")

            # DALL·E-3 이미지 생성
            image_response = await openai_client.images.generate(
                model="dall-e-3",
                prompt=f"""
                {text}의 내용과 같은 한 귀여운 따뜻한 그림일기 일러스트.
                초등학생이 쓴 일기에서 나온 장면처럼, 단순하고 밝은 만화 스타일. 
                가족들과의 일상 경험을 표현,
                내용에 들어가지 않는 가상의 인물들은 추가하지 마세요,
                최대한 그림에 언어를 넣지 마세요.
                종교 관련 이미지도 제외해주세요.
                """,
                size="1024x1024",
                n=1
            )
            openai_image_url = image_response.data[0].url
            
            # OpenAI에서 받은 이미지를 S3에 임시 저장
            s3_image_url = await download_and_upload_image_to_s3(openai_image_url, is_temp=True)

            return {
                "title": diary_dict.get("title", ""),
                "content": diary_dict.get("content", ""),
                "image_url": s3_image_url
            }

        except Exception as e:
            logging.error(f"일기 생성 실패: {str(e)}")
            return {
                "title": "처리 실패",
                "content": "처리 실패",
                "image_url": ""
            }


class VideoAIService:
    """영상화 AI 서비스"""
    
    @staticmethod
    async def animate_image(image_url: str, prompt: str):
        """사진을 영상화"""
        # API 키 상태 확인
        if not modelslab_api_key:
            logging.error("MODELSLAB_API_KEY가 설정되지 않았습니다.")
            return {
                "video_url": "",
                "status": "error",
                "message": "ModelsLab API 키가 설정되지 않았습니다."
            }
        
        logging.info(f"ModelsLab API 키 상태: {'설정됨' if modelslab_api_key else '설정되지 않음'}")
        logging.info(f"ModelsLab API 키 길이: {len(modelslab_api_key) if modelslab_api_key else 0}자")
        
        max_retries = 3
        retry_delay = 2  # 초
        
        for attempt in range(max_retries):
            try:
                logging.info(f"영상화 시작 (시도 {attempt + 1}/{max_retries}): 이미지 URL: {image_url}, 프롬프트: {prompt[:100]}")
                
                # ModelsLab API로 영상화 요청 (URL만 받기)
                video_url = await VideoAIService._call_modelslab_api(image_url, prompt)
                
                # ModelsLab에서 받은 비디오를 S3에 다운로드하여 저장
                s3_video_url = await VideoAIService._download_and_upload_to_s3(video_url)
                
                logging.info(f"영상화 완료: S3 URL - {s3_video_url}")
                
                return {
                    "video_url": s3_video_url,
                    "status": "success", 
                    "message": "영상화가 완료되었습니다."
                }
                
            except Exception as e:
                error_msg = str(e)
                logging.error(f"영상화 실패 (시도 {attempt + 1}/{max_retries}): {error_msg}")
                
                # 마지막 시도가 아니고, 재시도 가능한 에러인 경우
                if attempt < max_retries - 1 and "Failed to generate image" in error_msg:
                    logging.info(f"{retry_delay}초 후 재시도합니다...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # 지수 백오프
                    continue
                else:
                    return {
                        "video_url": "",
                        "status": "error",
                        "message": f"영상화 처리 중 오류가 발생했습니다: {error_msg}"
                    }
    
    @staticmethod
    async def _call_modelslab_api(image_url: str, prompt: str) -> str:
        """ModelsLab API를 호출하여 실제 영상화 수행"""
        if not modelslab_api_key:
            raise ValueError("MODELSLAB_API_KEY 환경 변수가 설정되지 않았습니다.")
        
        url = "https://modelslab.com/api/v7/video-fusion/image-to-video"
        
        headers = {
            "key": modelslab_api_key,
            "Content-Type": "application/json"
        }
        
        data = {
            "key": modelslab_api_key,
            "model_id": "seedance-i2v",
            "init_image": image_url,
            "prompt": prompt
        }
        
        logging.info(f"ModelsLab API 호출 시작: {image_url}")
        logging.info(f"ModelsLab API 요청 데이터: {{'key': '***', 'model_id': '{data['model_id']}', 'init_image': '{data['init_image']}', 'prompt': '{data['prompt']}'}}")
        
        async with aiohttp.ClientSession() as session:
            # 1. 영상화 요청
            async with session.post(url, headers=headers, json=data) as response:
                logging.info(f"ModelsLab API 응답 상태: {response.status}")
                logging.info(f"ModelsLab API 응답 헤더: {dict(response.headers)}")
                
                if response.status != 200:
                    error_text = await response.text()
                    logging.error(f"ModelsLab API HTTP 오류: {response.status} - {error_text}")
                    raise Exception(f"ModelsLab API HTTP 오류 ({response.status}): {error_text}")
                
                result = await response.json()
                logging.info(f"ModelsLab API 응답: {result}")
                
                # 2. 응답 처리
                if result.get("status") == "error":
                    error_message = result.get("message", "알 수 없는 오류")
                    error_code = result.get("code", "unknown")
                    logging.error(f"ModelsLab API 에러: {error_message} (코드: {error_code})")
                    
                    # 특정 에러에 대한 상세 정보
                    if "Failed to generate image" in error_message:
                        logging.error("이미지 생성 실패 - 가능한 원인: 이미지 형식, 크기, 내용 등")
                        logging.error(f"이미지 URL: {image_url}")
                        logging.error(f"프롬프트: {prompt}")
                    
                    raise Exception(f"ModelsLab API 에러: {error_message}")
                
                elif result.get("status") == "processing":
                    # 처리 중인 경우 polling으로 결과 대기
                    task_id = result.get("id")
                    if not task_id:
                        raise Exception("ModelsLab API에서 task_id를 받지 못했습니다.")
                    
                    logging.info(f"ModelsLab API 처리 중 - task_id: {task_id}")
                    # polling으로 결과 대기
                    return await VideoAIService._poll_modelslab_result(session, modelslab_api_key, task_id)
                    
                elif result.get("output") and len(result.get("output", [])) > 0:
                    # 바로 결과가 온 경우
                    video_url = result.get("output")[0]
                    logging.info(f"ModelsLab 비디오 URL 받음: {video_url}")
                    return video_url
                else:
                    logging.error(f"ModelsLab API 응답에 output이 없음: {result}")
                    raise Exception("ModelsLab API에서 비디오 URL을 받지 못했습니다.")
    
    @staticmethod
    async def _poll_modelslab_result(session: aiohttp.ClientSession, api_key: str, task_id: int) -> str:
        """ModelsLab API 처리 결과를 polling으로 확인"""
        fetch_url = f"https://modelslab.com/api/v7/video-fusion/fetch/{task_id}"
        
        headers = {
            "key": api_key,
            "Content-Type": "application/json"
        }
        
        max_attempts = 7  # 최대 1분 10초 대기 (10초 * 7)
        
        for attempt in range(max_attempts):
            logging.info(f"ModelsLab 결과 확인 시도 {attempt + 1}/{max_attempts}")
            
            await asyncio.sleep(10)  # 10초 대기
            
            async with session.post(fetch_url, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"ModelsLab fetch API 오류 ({response.status}): {error_text}")
                
                result = await response.json()
                logging.info(f"Polling 응답: {result}")
                
                if result.get("status") == "error":
                    error_message = result.get("message", "알 수 없는 오류")
                    logging.error(f"ModelsLab API polling 에러: {error_message}")
                    raise Exception(f"ModelsLab API 처리 실패: {error_message}")
                
                elif result.get("output") and len(result.get("output", [])) > 0:
                    video_url = result.get("output")[0]
                    logging.info(f"ModelsLab 비디오 URL 받음: {video_url}")
                    return video_url
                
                elif result.get("status") != "processing":
                    raise Exception(f"ModelsLab API 처리 실패: {result.get('message', '알 수 없는 오류')}")
        
        raise Exception("ModelsLab API 처리 시간 초과 (1분)")
    
    @staticmethod
    async def _download_and_upload_to_s3(video_url: str) -> str:
        """ModelsLab에서 받은 비디오를 다운로드하여 S3에 업로드"""
        try:
            logging.info(f"비디오 다운로드 시작: {video_url}")
            
            async with aiohttp.ClientSession() as session:
                async with session.get(video_url) as response:
                    if response.status != 200:
                        raise Exception(f"비디오 다운로드 실패 ({response.status})")
                    
                    video_data = await response.read()
                    logging.info(f"비디오 다운로드 완료: {len(video_data)} bytes")
                    
                    # s3_util.py의 upload_video_to_s3 사용
                    s3_url = upload_video_to_s3(video_data, is_temp=True)
                    
                    logging.info(f"비디오 S3 업로드 완료: {s3_url}")
                    return s3_url
                    
        except Exception as e:
            logging.error(f"비디오 다운로드/업로드 실패: {str(e)}")
            raise Exception(f"비디오 처리 실패: {str(e)}")


class CharacterAIService:
    """캐릭터화 AI 서비스"""
    
    @staticmethod
    async def characterize_image(image_url: str, prompt: str = "Ghibli Studio style, Charming hand-drawn anime-style illustration"):
        """이미지를 캐릭터화"""
        # API 키 상태 확인
        if not modelslab_api_key:
            logging.error("MODELSLAB_API_KEY가 설정되지 않았습니다.")
            return {
                "character_image_url": "",
                "status": "error",
                "message": "ModelsLab API 키가 설정되지 않았습니다."
            } 
        
        logging.info(f"캐릭터화 시작: 이미지 URL: {image_url}, 프롬프트: {prompt[:100]}")
        
        max_retries = 3
        retry_delay = 2  # 초
        
        for attempt in range(max_retries):
            try:
                logging.info(f"캐릭터화 시작 (시도 {attempt + 1}/{max_retries})")
                
                # ModelsLab API로 캐릭터화 요청
                character_image_url = await CharacterAIService._call_modelslab_characterize_api(image_url, prompt)
                
                # ModelsLab에서 받은 이미지를 S3에 다운로드하여 저장
                s3_character_image_url = await CharacterAIService._download_and_upload_character_to_s3(character_image_url)
                
                logging.info(f"캐릭터화 완료: S3 URL - {s3_character_image_url}")
                
                return {
                    "character_image_url": s3_character_image_url,
                    "status": "success", 
                    "message": "캐릭터화가 완료되었습니다."
                }
                
            except Exception as e:
                error_msg = str(e)
                logging.error(f"캐릭터화 실패 (시도 {attempt + 1}/{max_retries}): {error_msg}")
                
                # 마지막 시도가 아니고, 재시도 가능한 에러인 경우
                if attempt < max_retries - 1 and "Failed to generate image" in error_msg:
                    logging.info(f"{retry_delay}초 후 재시도합니다...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # 지수 백오프
                    continue
                else:
                    return {
                        "character_image_url": "",
                        "status": "error",
                        "message": f"캐릭터화 처리 중 오류가 발생했습니다: {error_msg}"
                    }
    
    @staticmethod
    async def _call_modelslab_characterize_api(image_url: str, prompt: str) -> str:
        """ModelsLab ControlNet API를 호출하여 캐릭터화 수행"""
        if not modelslab_api_key:
            raise ValueError("MODELSLAB_API_KEY 환경 변수가 설정되지 않았습니다.")
        
        url = "https://modelslab.com/api/v5/controlnet"
        
        headers = {
            "Content-Type": "application/json"
        }
        
        data = {
            "model_id": "fluxdev",
            "init_image": image_url,
            "prompt": prompt,
            "steps": "20",
            "controlnet_type": "ghibli",
            "controlnet_model": "ghibli",
            "key": modelslab_api_key
        }
        
        logging.info(f"ModelsLab ControlNet API 호출 시작: {image_url}")
        logging.info(f"ModelsLab ControlNet API 요청 데이터: {{'model_id': '{data['model_id']}', 'init_image': '{data['init_image']}', 'prompt': '{data['prompt']}', 'steps': '{data['steps']}', 'controlnet_type': '{data['controlnet_type']}', 'controlnet_model': '{data['controlnet_model']}', 'key': '***'}}")
        
        async with aiohttp.ClientSession() as session:
            # 캐릭터화 요청
            async with session.post(url, headers=headers, json=data) as response:
                logging.info(f"ModelsLab ControlNet API 응답 상태: {response.status}")
                logging.info(f"ModelsLab ControlNet API 응답 헤더: {dict(response.headers)}")
                
                if response.status != 200:
                    error_text = await response.text()
                    logging.error(f"ModelsLab ControlNet API HTTP 오류: {response.status} - {error_text}")
                    raise Exception(f"ModelsLab ControlNet API HTTP 오류 ({response.status}): {error_text}")
                
                result = await response.json()
                logging.info(f"ModelsLab ControlNet API 응답: {result}")
                
                # 응답 처리
                if result.get("status") == "error":
                    error_message = result.get("message", "알 수 없는 오류")
                    error_code = result.get("code", "unknown")
                    logging.error(f"ModelsLab ControlNet API 에러: {error_message} (코드: {error_code})")
                    raise Exception(f"ModelsLab ControlNet API 에러: {error_message}")
                
                elif result.get("status") == "processing":
                    # 처리 중인 경우 polling으로 결과 대기
                    task_id = result.get("id")
                    if not task_id:
                        raise Exception("ModelsLab ControlNet API에서 task_id를 받지 못했습니다.")
                    
                    logging.info(f"ModelsLab ControlNet API 처리 중 - task_id: {task_id}")
                    # polling으로 결과 대기
                    return await CharacterAIService._poll_modelslab_characterize_result(session, modelslab_api_key, task_id)
                    
                elif result.get("output") and len(result.get("output", [])) > 0:
                    # 바로 결과가 온 경우
                    character_image_url = result.get("output")[0]
                    logging.info(f"ModelsLab 캐릭터 이미지 URL 받음: {character_image_url}")
                    return character_image_url
                else:
                    logging.error(f"ModelsLab ControlNet API 응답에 output이 없음: {result}")
                    raise Exception("ModelsLab ControlNet API에서 캐릭터 이미지 URL을 받지 못했습니다.")
    
    @staticmethod
    async def _poll_modelslab_characterize_result(session: aiohttp.ClientSession, api_key: str, task_id: int) -> str:
        """ModelsLab ControlNet API 처리 결과를 polling으로 확인"""
        fetch_url = f"https://modelslab.com/api/v5/controlnet/fetch/{task_id}"
        
        headers = {
            "key": api_key,
            "Content-Type": "application/json"
        }
        
        max_attempts = 7  # 최대 1분 10초 대기 (10초 * 7)
        
        for attempt in range(max_attempts):
            logging.info(f"ModelsLab ControlNet 결과 확인 시도 {attempt + 1}/{max_attempts}")
            
            await asyncio.sleep(10)  # 10초 대기
            
            async with session.post(fetch_url, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"ModelsLab ControlNet fetch API 오류 ({response.status}): {error_text}")
                
                result = await response.json()
                logging.info(f"ControlNet Polling 응답: {result}")
                
                if result.get("status") == "error":
                    error_message = result.get("message", "알 수 없는 오류")
                    logging.error(f"ModelsLab ControlNet API polling 에러: {error_message}")
                    raise Exception(f"ModelsLab ControlNet API 처리 실패: {error_message}")
                
                elif result.get("output") and len(result.get("output", [])) > 0:
                    character_image_url = result.get("output")[0]
                    logging.info(f"ModelsLab 캐릭터 이미지 URL 받음: {character_image_url}")
                    return character_image_url
                
                elif result.get("status") != "processing":
                    raise Exception(f"ModelsLab ControlNet API 처리 실패: {result.get('message', '알 수 없는 오류')}")
        
        raise Exception("ModelsLab ControlNet API 처리 시간 초과 (1분)")
    
    @staticmethod
    async def _download_and_upload_character_to_s3(character_image_url: str) -> str:
        """ModelsLab에서 받은 캐릭터 이미지를 다운로드하여 S3에 업로드"""
        try:
            logging.info(f"캐릭터 이미지 다운로드 시작: {character_image_url}")
            
            async with aiohttp.ClientSession() as session:
                async with session.get(character_image_url) as response:
                    if response.status != 200:
                        raise Exception(f"캐릭터 이미지 다운로드 실패 ({response.status})")
                    
                    character_image_data = await response.read()
                    logging.info(f"캐릭터 이미지 다운로드 완료: {len(character_image_data)} bytes")
                    
                    # s3_util.py의 upload_image_to_s3 사용 (character 디렉토리)
                    s3_url = upload_image_to_s3(character_image_data, "character", "png")
                    
                    logging.info(f"캐릭터 이미지 S3 업로드 완료: {s3_url}")
                    return s3_url
                    
        except Exception as e:
            logging.error(f"캐릭터 이미지 다운로드/업로드 실패: {str(e)}")
            raise Exception(f"캐릭터 이미지 처리 실패: {str(e)}")

