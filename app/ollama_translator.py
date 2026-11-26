import ollama
import logging
from typing import Optional
import sys
import json

# Khắc phục lỗi in tiếng Việt trên console Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

# Thiết lập logging cơ bản để dễ debug
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class OllamaTranslatorAPI:
    """
    Class API mở cung cấp dịch vụ dịch thuật sử dụng LLM qua Ollama.
    Các crawler hoặc module khác có thể dễ dàng import và gọi đến class này.
    """
    
    def __init__(self, model_name: str = 'llama3'):
        """
        Khởi tạo dịch vụ dịch thuật.
        
        Args:
            model_name (str): Tên mô hình đang chạy trong Ollama (mặc định: 'llama3')
        """
        self.model_name = model_name
        logger.info(f"Khởi tạo Dịch vụ Dịch thuật với mô hình: {self.model_name}")

    def is_service_available(self) -> bool:
        """
        Kiểm tra xem service Ollama có đang chạy và mô hình có sẵn sàng không.
        """
        try:
            # Gọi thử một lệnh list để kiểm tra kết nối
            res = ollama.list()
            if hasattr(res, 'models'):
                return any(m.model.startswith(self.model_name) for m in res.models)
            elif isinstance(res, dict):
                return any(m.get('name', '').startswith(self.model_name) for m in res.get('models', []))
            return False
        except Exception as e:
            logger.error(f"Ollama không phản hồi. Vui lòng kiểm tra lại dịch vụ cục bộ. Lỗi: {e}")
            return False

    def translate_en_to_vi(self, text: str, domain_context: Optional[str] = None) -> str:
        """
        Dịch văn bản tiếng Anh sang tiếng Việt.
        
        Args:
            text (str): Đoạn văn bản tiếng Anh cần dịch.
            domain_context (str, optional): Ngữ cảnh chuyên ngành (vd: "Tự động hóa công nghiệp", ). 
                                            Giúp bản dịch sát nghĩa chuyên môn hơn.
        
        Returns:
            str: Bản dịch tiếng Việt.
        """
        if not text or not text.strip():
            return ""

        # Tuỳ biến Prompt dựa theo ngữ cảnh chuyên ngành (nếu có)
        context_instruction = ""
        if domain_context:
            context_instruction = f"Context: This text is related to '{domain_context}'. Please use appropriate technical terms."

        prompt = f"""
        Translate the following text into Vietnamese.
        {context_instruction}
        
        CRITICAL RULES:
        1. YOU MUST OUTPUT ONLY THE TRANSLATED TEXT.
        2. DO NOT add any notes, explanations, or conversational filler like "Here is the translation" or "(Note:...)".
        3. Keep it strictly professional and natural.

        Original text: "{text}"
        Vietnamese translation:"""

        try:
            response = ollama.chat(
                model=self.model_name,
                messages=[
                    {
                        'role': 'system',
                        'content': 'You are a translation API. You only output raw translated text without any explanation or conversational text.'
                    },
                    {
                        'role': 'user',
                        'content': prompt.strip()
                    }
                ]
            )
            return response['message']['content'].strip()
            
        except Exception as e:
            logger.error(f"Lỗi khi dịch đoạn văn bản '{text[:30]}...': {e}")
            # Trả lại bản gốc nếu lỗi, giúp các crawler không bị crash
            return text 

    def translate_batch(self, texts: list[str], domain_context: Optional[str] = None) -> list[str]:
        """
        Tiện ích dịch một mảng nhiều câu/đoạn văn bản cùng lúc.
        Dành cho các crawler cần dịch nhiều nội dung.
        """
        translated_list = []
        for text in texts:
            result = self.translate_en_to_vi(text, domain_context)
            translated_list.append(result)
        return translated_list

    def translate_specs(self, specs: list[tuple[str, str]], domain_context: Optional[str] = None) -> list[tuple[str, str]]:
        """
        Dịch toàn bộ mảng thông số kỹ thuật (gồm cả Key và Value).
        Chia nhỏ thành các batch nhỏ để LLM không bị bỏ sót khi dữ liệu quá dài.
        """
        if not specs:
            return specs
            
        chunk_size = 15
        final_translated = []
        
        for i in range(0, len(specs), chunk_size):
            chunk = specs[i:i + chunk_size]
            spec_list = [[k, v] for k, v in chunk]
            json_str = json.dumps(spec_list, ensure_ascii=False)
            
            prompt = f"""
            Translate the following JSON array of technical specifications into Vietnamese.
            Context: {domain_context or 'Industrial Automation'}
            
            CRITICAL RULES:
            1. YOU MUST OUTPUT ONLY VALID JSON.
            2. Do not add any conversational text, explanations or markdown blocks (like ```json).
            3. Translate both the first element (Key) and the second element (Value) in each sub-array.

            Original JSON:
            {json_str}
            
            Translated JSON:
            """
            
            try:
                response = ollama.chat(
                    model=self.model_name,
                    messages=[
                        {
                            'role': 'system',
                            'content': 'You are a strict API. You output ONLY valid JSON format. No markdown, no explanations.'
                        },
                        {
                            'role': 'user',
                            'content': prompt.strip()
                        }
                    ]
                )
                translated_text = response['message']['content'].strip()
                
                # Xử lý dọn dẹp nếu Llama cố tình bọc markdown
                if translated_text.startswith('```json'):
                    translated_text = translated_text[7:]
                if translated_text.startswith('```'):
                    translated_text = translated_text[3:]
                if translated_text.endswith('```'):
                    translated_text = translated_text[:-3]
                    
                translated_data = json.loads(translated_text.strip())
                
                # Kiểm tra xem dữ liệu trả về có hợp lệ không
                if isinstance(translated_data, list) and len(translated_data) == len(chunk):
                    final_translated.extend([(str(item[0]), str(item[1])) for item in translated_data])
                else:
                    logger.warning(f"Kết quả dịch chunk {i//chunk_size} không khớp số lượng. Fallback chunk này về bản gốc.")
                    final_translated.extend(chunk)
                    
            except Exception as e:
                logger.error(f"Lỗi khi dịch chunk {i//chunk_size}: {e}")
                final_translated.extend(chunk)
                
        return final_translated

# Ví dụ cách sử dụng (chỉ chạy khi gọi trực tiếp file này)
if __name__ == "__main__":
    translator = OllamaTranslatorAPI(model_name='llama3')
    
    # 1. Kiểm tra kết nối
    if translator.is_service_available():
        print("Dịch vụ Ollama đang hoạt động bình thường!\n")
        
        # 2. Dịch thông thường
        sample = "The proximity sensor detects the presence of objects without physical contact."
        print(f"Bản gốc: {sample}")
        print(f"Bản dịch: {translator.translate_en_to_vi(sample, domain_context='Industrial Automation')}")
    else:
        print("Vui lòng bật Ollama và tải mô hình Llama3 trước!")
