import asyncio, uuid, json, logging, re
from pathlib import Path
from fastapi import FastAPI, Depends, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from .config import get_settings
from .auth import get_current_user, CurrentUser
from .models import ChatRequest, ChatResponse, UserResponse, DocumentResponse, TranscriptionResponse, MemoryRequest
from .db import db_execute, db_fetchall, db_fetchone
from .llm import classify_intent, get_llm
from .rag import RAGService
from .storage import ObjectStorage
from .document import extract_salary_scale_markdown, salary_scale_markdown_from_text, extract_text, chunk_text, sanitize_text
from .voice import transcribe_audio, synthesize_piper

logging.basicConfig(level=logging.INFO)
s=get_settings(); app=FastAPI(title=s.app_name, version='1.0.0')
app.add_middleware(CORSMiddleware, allow_origins=[s.web_origin], allow_credentials=True, allow_methods=['*'], allow_headers=['*'])

rag=None; storage=None

def quick_reply(message: str) -> str | None:
    normalized=re.sub(r"[^a-zàâçéèêëîïôûùüÿñæœ0-9 ]", "", message.lower()).strip()
    greetings={"hi","hello","hey","bonjour","salut","bonsoir","good morning","good afternoon","good evening"}
    if normalized in greetings:
        return "Bonjour ! Comment puis-je vous aider ?"
    if normalized in {"merci","thanks","thank you"}:
        return "Avec plaisir !"
    return None

def is_document_catalog_request(message: str) -> bool:
    normalized=re.sub(r"[^a-zàâçéèêëîïôûùüÿñæœ0-9 ]", "", message.lower()).strip()
    catalog_terms=('document', 'source', 'référence', 'reference')
    state_terms=('disponible', 'upload', 'charg', 'présent', 'present')
    return any(term in normalized for term in catalog_terms) and any(term in normalized for term in state_terms)

def supports_catalog_intent(message: str) -> bool:
    normalized=message.lower()
    catalog_signals=('disponible', 'disponibles', 'chargé', 'chargés', 'chargées', 'chargées', 'upload', 'référence', 'reference', 'fichier', 'fichiers', 'liste', 'ressource', 'ressources')
    return any(signal in normalized for signal in catalog_signals)

def is_knowledge_question(message: str) -> bool:
    normalized=message.lower().strip()
    question_words=('qui', 'que', 'quoi', 'quel', 'quelle', 'quels', 'quelles', 'comment', 'pourquoi', 'où', 'quand', 'combien')
    action_words=('explique', 'décris', 'décrire', 'donne-moi', 'donne moi', 'liste', 'présente')
    return '?' in normalized or normalized.startswith(question_words) or normalized.startswith(action_words)

def is_topic_request(message: str) -> bool:
    words=message.lower().strip().split()
    personal_starts=('je ', "j'", 'j’', 'nous ', 'il ', 'elle ', 'ils ', 'elles ', 'mon ', 'ma ', 'mes ')
    return 1 < len(words) <= 8 and not message.lower().strip().startswith(personal_starts)

def requests_salary_table(message: str) -> bool:
    normalized=message.lower()
    return any(term in normalized for term in ('grille salariale', 'grille de salaire', 'grille des salaires', 'grille des rémunérations', 'grille chiffr', 'salary scale'))

def is_eneo_convention(filename: str) -> bool:
    normalized=filename.casefold()
    return 'eneo' in normalized and 'convention' in normalized
@app.on_event('startup')
async def startup():
    global rag,storage
    rag=RAGService(); storage=ObjectStorage()

async def ensure_user(user: CurrentUser):
    row=await db_fetchone('SELECT id FROM users WHERE external_id=:e',{'e':user.external_id})
    if not row:
        await db_execute('INSERT INTO users(external_id,name,email,department,role) VALUES(:e,:n,:m,:d,:r)',{'e':user.external_id,'n':user.name,'m':user.email,'d':user.department,'r':user.role})
    return await db_fetchone('SELECT id FROM users WHERE external_id=:e',{'e':user.external_id})

@app.get('/api/v1/health')
async def health(): return {'status':'ok','service':'enterprise-ai','llm_mode':s.llm_mode}

@app.get('/api/v1/me',response_model=UserResponse)
async def me(user: CurrentUser=Depends(get_current_user)):
    await ensure_user(user); return UserResponse(external_id=user.external_id,name=user.name,email=user.email,role=user.role,department=user.department)

@app.get('/api/v1/conversations')
async def conversations(user: CurrentUser=Depends(get_current_user)):
    u=await ensure_user(user)
    rows=await db_fetchall('SELECT id,title,created_at,updated_at FROM conversations WHERE user_id=:u ORDER BY updated_at DESC',{'u':u['id']})
    return [dict(r) for r in rows]

@app.get('/api/v1/conversations/{conversation_id}')
async def conversation(conversation_id:str,user:CurrentUser=Depends(get_current_user)):
    u=await ensure_user(user)
    rows=await db_fetchall('''SELECT m.id,m.role,m.content,m.metadata,m.created_at FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE c.id=:c AND c.user_id=:u ORDER BY m.created_at''',{'c':conversation_id,'u':u['id']})
    return [dict(r) for r in rows]

@app.post('/api/v1/chat',response_model=ChatResponse)
async def chat(req:ChatRequest,user:CurrentUser=Depends(get_current_user)):
    u=await ensure_user(user)
    cid=req.conversation_id
    if cid:
        conv=await db_fetchone('SELECT id FROM conversations WHERE id=:c AND user_id=:u',{'c':cid,'u':u['id']})
        if not conv: raise HTTPException(404,'Conversation not found')
    else:
        cid=str(uuid.uuid4()); await db_execute('INSERT INTO conversations(id,user_id,title) VALUES(:id,:u,:t)',{'id':cid,'u':u['id'],'t':req.message[:80]})
    history=await db_fetchall('SELECT role,content FROM messages WHERE conversation_id=:c ORDER BY created_at DESC LIMIT :lim',{'c':cid,'lim:s':s.max_history_messages} if False else {'c':cid,'lim':s.max_history_messages})
    history=list(reversed([{'role':x['role'],'content':x['content']} for x in history]))
    citations=[]; context=''; cited_documents=set(); retrieved_chunks=[]
    answer=quick_reply(req.message)
    catalog_request=is_document_catalog_request(req.message)
    intent=None
    direct_knowledge=is_knowledge_question(req.message) or is_topic_request(req.message)
    if answer is None and not catalog_request and not direct_knowledge and req.use_knowledge and s.intent_routing_enabled and len(req.message) <= 180:
        intent=await classify_intent(req.message, history)
        catalog_request=intent == 'document_catalog' and supports_catalog_intent(req.message)
        if intent == 'document_catalog' and not catalog_request:
            intent='knowledge_question'
    salary_table=requests_salary_table(req.message)
    if direct_knowledge:
        intent='knowledge_question'
    if answer is None and catalog_request:
        rows=await db_fetchall('SELECT filename,status FROM documents ORDER BY created_at DESC')
        if rows:
            answer='Documents sources disponibles :\n\n'+'\n'.join(f"- {row['filename']} ({row['status']})" for row in rows)
        else:
            answer='Aucun document source n’est disponible pour le moment.'
    search_knowledge=answer is None and req.use_knowledge and (intent == 'knowledge_question' or (intent is None and is_knowledge_question(req.message)))
    if search_knowledge:
        try:
            retrieval_limit=1 if any(term in req.message.lower() for term in ('grille', 'salaire', 'salary', 'wage')) else s.max_context_chunks
            previous_topics=' '.join(item['content'] for item in history if item['role'] == 'user')[-1000:]
            retrieval_query=f'{previous_topics} {req.message}'.strip()
            retrieval_limit=1 if any(term in retrieval_query.lower() for term in ('grille', 'salaire', 'salary', 'wage')) else retrieval_limit
            hits=rag.search(retrieval_query,retrieval_limit)
            unique_hits=[]
            for h in hits:
                p=h.payload or {}; document_key=str(p.get('filename') or p.get('document_id') or '').casefold()
                if document_key in cited_documents:
                    continue
                cited_documents.add(document_key); unique_hits.append(h); retrieved_chunks.append(p)
                citations.append({'document_id':p.get('document_id',''),'filename':p.get('filename',''),'chunk_index':p.get('chunk_index',0),'score':float(getattr(h,'score',0.0) or 0.0)})
            context_limit=2200 if salary_table else 800
            context_parts=[]
            for p in [h.payload or {} for h in unique_hits]:
                raw_content=str(p.get('content',''))
                content=raw_content if salary_table else ' '.join(raw_content.split())
                if salary_table:
                    marker=content.lower().find('appendix 3 salary scale')
                    if marker >= 0:
                        content=content[marker:]
                context_parts.append(f"[Source: {p.get('filename')} / chunk {p.get('chunk_index')}]\n{content[:context_limit]}")
            context='\n\n'.join(context_parts)
        except Exception as e: logging.warning('RAG unavailable: %s',e)
    if salary_table and retrieved_chunks:
        structured_table=salary_scale_markdown_from_text('\n'.join(str(item.get('content','')) for item in retrieved_chunks))
        if structured_table:
            answer='Voici la grille salariale extraite de l’Appendice 3 :\n\n'+structured_table
        document_id=retrieved_chunks[0].get('document_id')
        document_row=await db_fetchone('SELECT object_key FROM documents WHERE id=:d',{'d':document_id}) if document_id else None
        if answer is None and document_row:
            structured_table=extract_salary_scale_markdown(storage.get(document_row['object_key']))
            if structured_table:
                answer='Voici la grille salariale extraite de l’Appendice 3 :\n\n'+structured_table
    system='''You are Enterprise AI, a sovereign internal employee assistant. Answer clearly and safely. Never invent company policy. In this knowledge base, SOCADEL is the new name of former ENEO; treat both names as the same organization. If knowledge sources are provided, prioritize them. Answer in the language of the user's latest message: French for French questions, English for English questions. Use the conversation history to resolve follow-ups such as "cette grille" or "this scale". For broad summary questions, give at most 3 numbered items, with each description limited to 12 words. End every sentence completely. Do not include a source list or repeat document filenames in your answer; the interface displays sources separately. If evidence is insufficient, say so. Do not expose confidential information outside the user's authorized context.'''
    if requests_salary_table(req.message):
        system += '\nFor a salary-scale request, use only the APPENDIX 3 SALARY SCALE section, never the job classification matrix. Reproduce every available numeric row from that section as a Markdown table. Use columns Echelon, Minimum, Médian, Maximum. Preserve the source numbers exactly and do not invent missing values; write "non lisible" where the PDF extraction does not establish a value. Add one brief note if the source layout is ambiguous.'
    if context: system += '\n\nEnterprise knowledge:\n'+context
    if answer is None:
        messages=[{'role':'system','content':system}]+history+[{'role':'user','content':req.message}]
        try:
            answer=await asyncio.wait_for(get_llm().chat(messages), timeout=s.chat_timeout_seconds)
        except Exception as e:
            if isinstance(e, asyncio.TimeoutError) and retrieved_chunks:
                answer='Je n’ai pas pu produire une réponse fiable dans le délai imparti. Veuillez réessayer.'
            else:
                logging.exception('LLM request failed')
                raise HTTPException(503, f'Le modèle est indisponible ou n’a pas répondu à temps: {e}')
    mid=str(uuid.uuid4())
    await db_execute('INSERT INTO messages(id,conversation_id,role,content,metadata) VALUES(:id,:c,:r,:x,:m)',{'id':str(uuid.uuid4()),'c':cid,'r':'user','x':req.message,'m':'{}'})
    await db_execute('INSERT INTO messages(id,conversation_id,role,content,metadata) VALUES(:id,:c,:r,:x,:m)',{'id':mid,'c':cid,'r':'assistant','x':answer,'m':json.dumps({'citations':citations})})
    await db_execute('UPDATE conversations SET updated_at=now() WHERE id=:c',{'c':cid})
    await db_execute('INSERT INTO audit_logs(user_external_id,action,resource,metadata) VALUES(:u,:a,:r,:m)',{'u':user.external_id,'a':'chat','r':cid,'m':json.dumps({'voice_response':req.voice_response})})
    return ChatResponse(conversation_id=cid,message_id=mid,answer=answer,citations=citations,metadata={'voice_available':s.tts_enabled})

@app.post('/api/v1/documents',response_model=DocumentResponse)
async def upload_document(file:UploadFile=File(...),user:CurrentUser=Depends(get_current_user)):
    data=await file.read()
    if len(data)>s.max_upload_mb*1024*1024: raise HTTPException(413,'File too large')
    filename=sanitize_text(file.filename or 'document')
    doc_id=str(uuid.uuid4()); key=f'{user.external_id}/{doc_id}/{filename}'
    try:
        if is_eneo_convention(filename):
            previous_documents=await db_fetchall("""SELECT id FROM documents
                WHERE lower(filename) LIKE '%eneo%'
                  AND lower(filename) LIKE '%convention%'""")
        else:
            previous_documents=await db_fetchall('SELECT id FROM documents WHERE filename=:f',{'f':filename})
        storage.put(key,data,file.content_type or 'application/octet-stream')
        text=extract_text(filename,data); chunks=chunk_text(text)
        if not chunks:
            raise ValueError('Impossible d’extraire du texte de ce document, y compris par OCR.')
        vector_ids=rag.index_chunks(doc_id,filename,chunks,{'owner':user.external_id}) if chunks else []
        await db_execute('INSERT INTO documents(id,filename,object_key,mime_type,size_bytes,status,metadata) VALUES(:id,:f,:o,:m,:s,:st,:md)',{'id':doc_id,'f':filename,'o':key,'m':file.content_type,'s':len(data),'st':'indexed','md':json.dumps({'owner':user.external_id,'chunks':len(chunks)})})
        for i,(chunk,vid) in enumerate(zip(chunks,vector_ids)):
            await db_execute('INSERT INTO document_chunks(document_id,chunk_index,content,vector_id) VALUES(:d,:i,:c,:v)',{'d':doc_id,'i':i,'c':chunk,'v':vid})
        for previous_document in previous_documents:
            previous_id=str(previous_document['id'])
            rag.delete_document(previous_id)
            await db_execute('DELETE FROM documents WHERE id=:d',{'d':previous_id})
        return DocumentResponse(id=doc_id,filename=filename,status='indexed',size_bytes=len(data))
    except Exception as e:
        logging.exception('document indexing failed'); raise HTTPException(500,str(e))

@app.get('/api/v1/documents',response_model=list[DocumentResponse])
async def documents(user:CurrentUser=Depends(get_current_user)):
    rows=await db_fetchall('SELECT id,filename,status,size_bytes FROM documents ORDER BY created_at DESC')
    return [DocumentResponse(id=str(r['id']),filename=r['filename'],status=r['status'],size_bytes=r['size_bytes']) for r in rows]

@app.post('/api/v1/memory')
async def add_memory(req:MemoryRequest,user:CurrentUser=Depends(get_current_user)):
    u=await ensure_user(user)
    mid=str(uuid.uuid4()); await db_execute('INSERT INTO memories(id,user_id,kind,content) VALUES(:id,:u,:k,:c)',{'id':mid,'u':u['id'],'k':req.kind,'c':req.content})
    return {'id':mid,'status':'stored'}

@app.get('/api/v1/memory')
async def list_memory(user:CurrentUser=Depends(get_current_user)):
    u=await ensure_user(user); rows=await db_fetchall('SELECT id,kind,content,created_at FROM memories WHERE user_id=:u ORDER BY created_at DESC',{'u':u['id']}); return [dict(r) for r in rows]

@app.post('/api/v1/voice/transcribe',response_model=TranscriptionResponse)
async def voice_transcribe(file:UploadFile=File(...),user:CurrentUser=Depends(get_current_user)):
    data=await file.read(); text,lang=transcribe_audio(data,Path(file.filename or 'audio.webm').suffix or '.webm'); return TranscriptionResponse(text=text,language=lang)

@app.post('/api/v1/voice/synthesize')
async def voice_synthesize(payload:dict,user:CurrentUser=Depends(get_current_user)):
    text=payload.get('text','')
    if not text: raise HTTPException(400,'text is required')
    try: audio=synthesize_piper(text)
    except RuntimeError as e: raise HTTPException(503,str(e))
    return Response(content=audio,media_type='audio/wav')
