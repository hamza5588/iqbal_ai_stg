# import os
# import logging
# from urllib.parse import quote
# from pathlib import Path
# from typing import Optional, Union
# from langchain_groq import ChatGroq
# # from langchain.text_splitters import RecursiveCharacterTextSplitter
# from langchain_text_splitters import RecursiveCharacterTextSplitter

# from langchain.chains.combine_documents import create_stuff_documents_chain
# from langchain_core.prompts import ChatPromptTemplate
# from langchain.chains import create_retrieval_chain
# from langchain_community.vectorstores import FAISS
# from langchain_community.embeddings import HuggingFaceEmbeddings

# from langchain_community.document_loaders import PyPDFLoader, PyPDFDirectoryLoader
# from langchain_nomic.embeddings import NomicEmbeddings
# from app.models.models import UserModel

# # Logging setup
# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger(__name__)


# class DocumentChatBot:
#     def __init__(self, user_id: Optional[int] = None, document_path: Optional[Union[str, Path]] = None):
#         self.user_id = user_id
        
#         # Get API keys from database if user_id is provided, otherwise from environment
#         if user_id:
#             user_model = UserModel(user_id)
#             self.groq_api_key = user_model.get_api_key()
#             if not self.groq_api_key:
#                 raise ValueError(f"User {user_id} does not have a GROQ API key set")
#         else:
#             self.groq_api_key = os.getenv('GROQ_API_KEY')
#             if not self.groq_api_key:
#                 raise ValueError("GROQ_API_KEY environment variable not set")
        
#         # NOMIC API key is still from environment as it's not user-specific
#         self.nomic_api_key = os.getenv('NOMIC_API_KEY')
#         if not self.nomic_api_key:
#             raise ValueError("NOMIC_API_KEY environment variable not set")
            
#         self.llm = ChatGroq(groq_api_key=self.groq_api_key, model_name="llama-3.3-70b-versatile")
#         self.prompt = self._create_prompt()
#         self.vectors = None
#         self.embeddings = None
#         self.awaiting_confirmation = False
#         self.support_phone = "+92 317 5161606"
#         self.default_message = "Hi, I need help with my issue."
#         self.document_path = self._resolve_document_path(document_path)
#         self._initialize_embeddings()

#     def get_whatsapp_url(self) -> str:
#         """Generate properly formatted WhatsApp URL"""
#         # Remove all non-digit characters
#         clean_phone = ''.join(c for c in self.support_phone if c.isdigit())
#         if clean_phone.startswith('92'):
#             phone_number = clean_phone
#         else:
#             phone_number = '92' + clean_phone.lstrip('0')
        
#         # Encode the message
#         encoded_message = quote(self.default_message)
#         return f"https://wa.me/{phone_number}?text={encoded_message}"

#     def _resolve_document_path(self, document_path: Optional[Union[str, Path]]) -> Path:
#         if document_path:
#             doc_path = Path(document_path)
#         else:
#             if os.getenv('DOCKER_ENV') == 'true':
#                 doc_path = Path('/app/app/IqbalAI_FAQ.pdf')
#             else:
#                 doc_path = Path(__file__).parent.parent / 'app' / 'IqbalAI_FAQ.pdf'

#         if not doc_path.exists():
#             fallback_paths = [
#                 Path('/app/IqbalAI_FAQ.pdf'),
#                 Path('app/IqbalAI_FAQ.pdf'),
#                 Path(__file__).parent / 'IqbalAI_FAQ.pdf'
#             ]
#             for alt in fallback_paths:
#                 if alt.exists():
#                     return alt
#             raise FileNotFoundError(
#                 f"PDF file not found at any known location.\nChecked: {doc_path} and alternatives."
#             )
#         return doc_path

#     def _create_prompt(self) -> ChatPromptTemplate:
#         return ChatPromptTemplate.from_template("""
#        You are a helpful customer support assistant. Answer questions based on the context.
    
#     Response Format Rules:
#     - ALWAYS use proper bullet points (•) for lists, NEVER asterisks (*)
#     - Start each step with "• " (bullet and space)
#     - Put each step on a new line
#     - Keep responses concise

#     <context>
#     {context}
#     </context>

#     Question: {input}

#     Answer:""")

#     def _get_document_loader(self):
#         logger.info(f"Document path: {self.document_path}")
#         if not self.document_path.exists():
#             raise FileNotFoundError(f"Path does not exist: {self.document_path}")
#         if self.document_path.is_file() and self.document_path.suffix.lower() == '.pdf':
#             return PyPDFLoader(str(self.document_path))
#         elif self.document_path.is_dir():
#             return PyPDFDirectoryLoader(str(self.document_path))
#         else:
#             raise ValueError("Provided path must be a .pdf file or directory containing PDFs.")

#     def _initialize_embeddings(self):
#         try:
#             # self.embeddings = NomicEmbeddings(
#             #     model="nomic-embed-text-v1",
#             #     nomic_api_key=self.nomic_api_key
#             # )
#             self.embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")


#             loader = self._get_document_loader()
#             docs = loader.load()
#             if not docs:
#                 logger.warning("No documents found.")
#                 return

#             text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
#             split_docs = text_splitter.split_documents(docs)

#             self.vectors = FAISS.from_documents(split_docs, self.embeddings)
#             logger.info("Vector store initialized.")
#         except Exception as e:
#             logger.error(f"API connection failed: {str(e)}")
#             self.vectors = None
#             raise

#     def _handle_support_handoff(self, user_response: str) -> dict:
#         """Handle user response to support handoff request."""
#         user_response = user_response.lower().strip()
#         if user_response in ['yes', 'y', 'yeah', 'sure', 'ok', 'okay', 'redirect me']:
#             self.awaiting_confirmation = False
#             return {
#                 "redirect": True,
#                 "message": "I'm connecting you to our support team on WhatsApp...",
#                 "whatsapp_url": self.get_whatsapp_url()
#             }
#         elif user_response in ['no', 'n', 'not now']:
#             self.awaiting_confirmation = False
#             return {
#                 "redirect": False,
#                 "message": "No problem! How else can I assist you today?",
#                 "whatsapp_url": ""
#             }
#         else:
#             return {
#                 "redirect": False,
#                 "message": "I didn't understand. Would you like me to connect you with human support? (yes/no)",
#                 "whatsapp_url": ""
#             }

#     def get_response(self, question: str) -> dict:
#         """Get response from chatbot. Returns dict with response and redirect info."""
#         if not question.strip():
#             return {
#                 "redirect": False,
#                 "message": "Please ask a valid question.",
#                 "whatsapp_url": ""
#             }
            
#         if self.awaiting_confirmation:
#             return self._handle_support_handoff(question)
            
#         if not self.vectors:
#             return {
#                 "redirect": True,
#                 "message": "Our knowledge base is unavailable. Please contact our support team on WhatsApp.",
#                 "whatsapp_url": self.get_whatsapp_url()
#             }
            
#         try:
#             document_chain = create_stuff_documents_chain(self.llm, self.prompt)
#             retriever = self.vectors.as_retriever()
#             #used pipe operator
#             retrieval_chain = document_chain | retriever
            
#             retrieval_chain = create_retrieval_chain(retriever, document_chain)
#             response = retrieval_chain.invoke({'input': question})
#             bot_answer = response.get('answer', '')
#             formatted_answer = self._format_response(bot_answer)
            
#             # Check if the bot couldn't answer (fallback response)
#             fallback_phrases = [
#                 "I'm not sure", 
#                 "I don't know", 
#                 "Sorry", 
#                 "unable to help", 
#                 "I couldn't find",
#                 "connect you"
#             ]
            
#             if any(phrase.lower() in bot_answer.lower() for phrase in fallback_phrases):
#                 self.awaiting_confirmation = True
#                 return {
#                     "redirect": False,
#                     "message": "I couldn't find that information. Would you like me to connect you with our human support team on WhatsApp? (yes/no)",
#                     "whatsapp_url": ""
#                 }
                
#             return {
#                 "redirect": False,
#                 "message": formatted_answer,
#                 "whatsapp_url": ""
#             }
            
#         except Exception as e:
#             logger.error(f"Error during response generation: {e}")
#             return {
#                 "redirect": True,
#                 "message": "I'm having trouble answering that. You can contact our support team on WhatsApp.",
#                 "whatsapp_url": self.get_whatsapp_url()
#             }
        
#     def update_documents(self, new_path: Union[str, Path]):
#         self.document_path = Path(new_path)
#         self._initialize_embeddings()
    

#     def _format_response(self, text: str) -> str:
#         """Format the response text for better readability"""
#         text=text.replace("• ", "- ")
#         lines=text.split('\n')
#         formatted_lines=[]
#         for line in lines:
#             if line.strip().startswith(('1.', '2.', '3.', '4.', '5.')):
#                 line = "- " + line[2:].strip()
#             formatted_lines.append(line)
#         return '\n'.join(formatted_lines)
import os
import logging
from urllib.parse import quote
from pathlib import Path
from typing import Optional, Union
# from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from app.utils.llm_factory import create_llm

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.document_loaders import PyPDFLoader, PyPDFDirectoryLoader
from app.models.models import UserModel

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DocumentChatBot:
    def __init__(self, user_id: Optional[int] = None, document_path: Optional[Union[str, Path]] = None):
        self.user_id = user_id
        
        # Get LLM provider from system settings first
        from app.utils.db import get_db
        from app.models.database_models import SystemSettings
        db = get_db()
        setting = db.query(SystemSettings).filter(SystemSettings.key == 'llm_provider').first()
        provider = setting.value if setting else os.getenv('LLM_PROVIDER', 'openai').lower()
        
        # Get API keys from database if user_id is provided, otherwise from environment
        if user_id:
            # If OpenAI is set from admin, use environment variable instead of database key
            if provider == 'openai' and setting:
                self.groq_api_key = os.getenv('OPENAI_API_KEY')
            else:
                from app.models.database_models import User as DBUser
                user = db.query(DBUser).filter(DBUser.id == user_id).first()
                if user:
                    self.groq_api_key = user.groq_api_key or None
                else:
                    self.groq_api_key = None
                
                # If no API key in database, try to get from environment as fallback
                if not self.groq_api_key:
                    if provider == 'groq':
                        self.groq_api_key = os.getenv('GROQ_API_KEY')
                    elif provider == 'openai':
                        self.groq_api_key = os.getenv('OPENAI_API_KEY')
            
            # Enforce Groq-only when Groq is selected - no fallback to OpenAI
            if provider == 'groq' and not self.groq_api_key:
                raise ValueError(
                    f"User {user_id} does not have a Groq API key set. "
                    "Please configure your Groq API key in the chat interface. "
                    "Groq is currently selected as the LLM provider."
                )
        else:
            self.groq_api_key = os.getenv('GROQ_API_KEY')
            if provider == 'groq' and not self.groq_api_key:
                raise ValueError(
                    "GROQ_API_KEY environment variable is required when Groq is selected. "
                    "Please set GROQ_API_KEY in your environment or configure it in the chat interface."
                )
        
        # NOMIC API key is still from environment as it's not user-specific
        self.nomic_api_key = os.getenv('NOMIC_API_KEY')
        if not self.nomic_api_key:
            raise ValueError("NOMIC_API_KEY environment variable not set")
            
        # Use dynamic LLM factory - supports OpenAI, Groq, and vLLM
        # For OpenAI and Groq, use groq_api_key as the API key (legacy naming)
        # For vLLM, api_key is not needed
        # IMPORTANT: When Groq is selected, we ONLY use Groq - no fallback to OpenAI
        self.llm = create_llm(
            temperature=0.7,
            max_tokens=1024,
            api_key=self.groq_api_key if provider in ['openai', 'groq'] else None,
            provider=provider
        )
        self.prompt = self._create_prompt()
        self.vectors = None
        self.embeddings = None
        self.retriever = None
        self.chain = None
        self.awaiting_confirmation = False
        self.support_phone = "+92 317 5161606"
        self.default_message = "Hi, I need help with my issue."
        self.document_path = self._resolve_document_path(document_path)
        self._initialize_embeddings()

    def get_whatsapp_url(self) -> str:
        """Generate properly formatted WhatsApp URL"""
        # Remove all non-digit characters
        clean_phone = ''.join(c for c in self.support_phone if c.isdigit())
        if clean_phone.startswith('92'):
            phone_number = clean_phone
        else:
            phone_number = '92' + clean_phone.lstrip('0')
        
        # Encode the message
        encoded_message = quote(self.default_message)
        return f"https://wa.me/{phone_number}?text={encoded_message}"

    def _resolve_document_path(self, document_path: Optional[Union[str, Path]]) -> Path:
        if document_path:
            doc_path = Path(document_path)
        else:
            if os.getenv('DOCKER_ENV') == 'true':
                doc_path = Path('/app/app/IqbalAI_FAQ.pdf')
            else:
                doc_path = Path(__file__).parent.parent / 'app' / 'IqbalAI_FAQ.pdf'

        if not doc_path.exists():
            fallback_paths = [
                Path('/app/IqbalAI_FAQ.pdf'),
                Path('app/IqbalAI_FAQ.pdf'),
                Path(__file__).parent / 'IqbalAI_FAQ.pdf'
            ]
            for alt in fallback_paths:
                if alt.exists():
                    return alt
            raise FileNotFoundError(
                f"PDF file not found at any known location.\nChecked: {doc_path} and alternatives."
            )
        return doc_path

    def _create_prompt(self) -> ChatPromptTemplate:
        return ChatPromptTemplate.from_template("""
       You are a helpful customer support assistant. Answer questions based on the context.
    
    Response Format Rules:
    - ALWAYS use proper bullet points (•) for lists, NEVER asterisks (*)
    - Start each step with "• " (bullet and space)
    - Put each step on a new line
    - Keep responses concise

    <context>
    {context}
    </context>

    Question: {input}

    Answer:""")

    def _get_document_loader(self):
        logger.info(f"Document path: {self.document_path}")
        if not self.document_path.exists():
            raise FileNotFoundError(f"Path does not exist: {self.document_path}")
        if self.document_path.is_file() and self.document_path.suffix.lower() == '.pdf':
            return PyPDFLoader(str(self.document_path))
        elif self.document_path.is_dir():
            return PyPDFDirectoryLoader(str(self.document_path))
        else:
            raise ValueError("Provided path must be a .pdf file or directory containing PDFs.")

    def _format_docs(self, docs):
        """Format retrieved documents into a single string"""
        return "\n\n".join(doc.page_content for doc in docs)

    def _initialize_embeddings(self):
        try:
            self.embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

            loader = self._get_document_loader()
            docs = loader.load()
            if not docs:
                logger.warning("No documents found.")
                return

            text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
            split_docs = text_splitter.split_documents(docs)

            self.vectors = FAISS.from_documents(split_docs, self.embeddings)
            self.retriever = self.vectors.as_retriever()
            
            # Create the RAG chain using LCEL (pipe operator)
            self.chain = (
                {
                    "context": self.retriever | self._format_docs,
                    "input": RunnablePassthrough()
                }
                | self.prompt
                | self.llm
                | StrOutputParser()
            )
            
            logger.info("Vector store and RAG chain initialized.")
        except Exception as e:
            logger.error(f"API connection failed: {str(e)}")
            self.vectors = None
            self.chain = None
            raise

    def _handle_support_handoff(self, user_response: str) -> dict:
        """Handle user response to support handoff request."""
        user_response = user_response.lower().strip()
        if user_response in ['yes', 'y', 'yeah', 'sure', 'ok', 'okay', 'redirect me']:
            self.awaiting_confirmation = False
            return {
                "redirect": True,
                "message": "I'm connecting you to our support team on WhatsApp...",
                "whatsapp_url": self.get_whatsapp_url()
            }
        elif user_response in ['no', 'n', 'not now']:
            self.awaiting_confirmation = False
            return {
                "redirect": False,
                "message": "No problem! How else can I assist you today?",
                "whatsapp_url": ""
            }
        else:
            return {
                "redirect": False,
                "message": "I didn't understand. Would you like me to connect you with human support? (yes/no)",
                "whatsapp_url": ""
            }

    def get_response(self, question: str) -> dict:
        """Get response from chatbot. Returns dict with response and redirect info."""
        if not question.strip():
            return {
                "redirect": False,
                "message": "Please ask a valid question.",
                "whatsapp_url": ""
            }
            
        if self.awaiting_confirmation:
            return self._handle_support_handoff(question)
            
        if not self.chain:
            return {
                "redirect": True,
                "message": "Our knowledge base is unavailable. Please contact our support team on WhatsApp.",
                "whatsapp_url": self.get_whatsapp_url()
            }
            
        try:
            # Use the LCEL chain with pipe operator
            bot_answer = self.chain.invoke(question)
            formatted_answer = self._format_response(bot_answer)
            
            # Check if the bot couldn't answer (fallback response)
            fallback_phrases = [
                "I'm not sure", 
                "I don't know", 
                "Sorry", 
                "unable to help", 
                "I couldn't find",
                "connect you"
            ]
            
            if any(phrase.lower() in bot_answer.lower() for phrase in fallback_phrases):
                self.awaiting_confirmation = True
                return {
                    "redirect": False,
                    "message": "I couldn't find that information. Would you like me to connect you with our human support team on WhatsApp? (yes/no)",
                    "whatsapp_url": ""
                }
                
            return {
                "redirect": False,
                "message": formatted_answer,
                "whatsapp_url": ""
            }
            
        except Exception as e:
            logger.error(f"Error during response generation: {e}")
            return {
                "redirect": True,
                "message": "I'm having trouble answering that. You can contact our support team on WhatsApp.",
                "whatsapp_url": self.get_whatsapp_url()
            }
        
    def update_documents(self, new_path: Union[str, Path]):
        self.document_path = Path(new_path)
        self._initialize_embeddings()
    
    def _format_response(self, text: str) -> str:
        """Format the response text for better readability"""
        text = text.replace("• ", "- ")
        lines = text.split('\n')
        formatted_lines = []
        for line in lines:
            if line.strip().startswith(('1.', '2.', '3.', '4.', '5.')):
                line = "- " + line[2:].strip()
            formatted_lines.append(line)
        return '\n'.join(formatted_lines)