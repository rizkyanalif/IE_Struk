from flask import Flask, render_template, request, redirect, url_for, jsonify
import os
import fitz
import base64
from dotenv import load_dotenv
from pydantic import BaseModel
from openai import OpenAI
import json
import re
from typing import List

app = Flask(__name__)
app.json.sort_keys = False
load_dotenv()

BASE_URL = os.getenv("BASE_URL")
API_KEY = os.getenv("API_KEY")
MODEL = os.getenv("MODEL")
PROMPT = """
You are an Enterprise AI Vision Document Extractor.

Your task is to read the image or PDF document uploaded by the user, comprehend its content, and extract structured data to automatically populate form fields.

### GENERAL RULES:
1. Focus exclusively on the text visibly present in the document.
2. Do not hallucinate or fabricate data, unless explicitly instructed by the user to populate a specific field with a predefined value.
3. If a field is not found in the document, return null.
4. Nominal/currency values must be extracted as pure numbers without any currency symbols (e.g., return 15000 instead of Rp15.000 or $15).
5. Remove any leading, trailing, or excessive whitespace.

### SPECIFIC RULES:
* reasoning: Think step-by-step and provide your rationale for each data field you extract before outputting the final value.
* transaction_date: Must be formatted strictly as DD-MM-YYYY. The source date is already in a "day-month-year" structure; your only task is to standardize it into the DD-MM-YYYY format.
* item_price: This represents the unit price of a single item.
"""

data_list = []
validated_data = {}

@app.route("/")
def index():
    return render_template("index.html", text=None, path=None)

class Item(BaseModel):
      item_name: str | None
      item_price: int | None
      quantity: int | None
      subtotal: int | None

class Receipts(BaseModel):
      reasoning: str | None
      store_name: str | None
      transaction_date: str | None
      receipt_no: str | None
      items: List[Item] | None
      description: str | None
      total_paid: int | None

labels = {
    'store_name': 'Nama Toko',
    'transaction_date': 'Tanggal Transaksi',
    'receipt_no': 'Nomor Receipt',
    'item_name': 'Nama Item',
    'item_price': 'Harga Item',
    'quantity': 'Jumlah',
    'subtotal': 'Subtotal',
    'description': 'Deskripsi',
    'total_paid': 'Harga Total',
}

def extract_info(input, file_ext, additional_prompt=""):
    client = OpenAI(
        base_url=BASE_URL,
        api_key=API_KEY
    )
    content=[]
    content.append({"type": "text", "text": "Extract dokumen berikut"})
    final_prompt = PROMPT
    if(additional_prompt):
        print(f'##############################################################################################################\n{additional_prompt}\n##############################################################################################################')
        final_prompt += "\n\n### INSTRUKSI TAMBAHAN (PRIORITAS TERTINGGI - TIMPA ATURAN LAIN JIKA BERTENTANGAN):\n" + additional_prompt
    try:
        if isinstance(input, bytes):
            if file_ext == 'pdf':
                doc = fitz.open(stream=input, filetype="pdf")
                for page in doc:
                    pix = page.get_pixmap(dpi=150)
                    img_bytes = pix.tobytes("png")
                    
                    # Encode the image bytes to base64 string
                    base64_image = base64.b64encode(img_bytes).decode('utf-8')
                    
                    # Add to the content list in OpenAI format
                    content.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{base64_image}"
                        }
                    })
                doc.close()
            elif file_ext in ['png', 'jpg', 'jpeg']:
                base64_image = base64.b64encode(input).decode('utf-8')
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/{file_ext};base64,{base64_image}"}
                })
        elif isinstance(input, str):
            if not os.path.exists(input):
                raise FileNotFoundError(f"File tidak ditemukan: {input}")
            if file_ext == 'pdf':
                doc = fitz.open(input)
                for page in doc:
                    pix = page.get_pixmap(dpi=150)
                    img_bytes = pix.tobytes("png")
                    
                    # Encode the image bytes to base64 string
                    base64_image = base64.b64encode(img_bytes).decode('utf-8')
                    
                    # Add to the content list in OpenAI format
                    content.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{base64_image}"
                        }
                    })
                doc.close()
            elif file_ext in ['png', 'jpg', 'jpeg']:
                with open(input, 'rb') as img_file:
                    base64_image = base64.b64encode(img_file.read()).decode('utf-8')
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/{file_ext};base64,{base64_image}"}
                })
        else:
            raise ValueError("Input harus berupa file / path")
    finally:
        None
    response = client.beta.chat.completions.parse(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": final_prompt,
            },
            {
                "role": "user",
                "content": content,
            }
        ],
        temperature=0,
        response_format=Receipts,
    )

    print(response.choices[0].message.content)
    data = json.loads(response.choices[0].message.content)
    

    print(additional_prompt)
    return data


@app.route("/upload_single", methods=["POST"])
def upload_single():
    global data_list
    file = request.files.get("file")
    add_prompt = request.form.get('prompt')
    if not file:
        return jsonify({"error": "No file"}), 400

    file_ext = file.filename.lower().split('.')[-1]
    file_bytes = file.read()
    
    if file_ext in ['pdf', 'png', 'jpeg', 'jpg']:
        try:
            data = extract_info(file_bytes, file_ext, add_prompt)
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    else:
        return jsonify({"error": "Unsupported file type"}), 400

    data_list.append({
            'file_name': os.path.basename(file.filename),
            'data': data
        })
    
    return jsonify({"status": "success"})

@app.route("/submit", methods=["POST"])
def submit():
    global data_list, validated_data

    form_data = {key: value for key, value in request.form.items() if not key.endswith('[]')}
    i = int(form_data.pop("index"))
##################
    names = request.form.getlist('item_name[]')
    prices = request.form.getlist('item_price[]')
    qtys = request.form.getlist('item_qty[]')
    subtotals = request.form.getlist('item_subtotal[]')

    item_list = []
    for idx in range(len(names)):
        item_list.append({
            "item_name": names[idx],
            "item_price": int(prices[idx]) if prices[idx].isdigit() else 0,
            "quantity": int(qtys[idx]) if qtys[idx].isdigit() else 0,
            "subtotal": int(subtotals[idx]) if subtotals[idx].isdigit() else 0
        })
    
    form_data['items'] = item_list
    form_data['total_paid'] = int(form_data['total_paid'])
    
    # # 4. Satukan ke struktur data utama
    # updated_data = {
    #     "store_name": store_name,
    #     "transaction_date": transaction_date,
    #     "receipt_no": receipt_no,
    #     "items": reconstructed_items,
    #     "description": description,
    #     "total_paid": int(total_paid) if total_paid.isdigit() else 0
    # }
##################
    if i < len(data_list):
        data_list[i]['submitted'] = True
    upper_data = {k: v for k,v in form_data.items()} # v.upper untuk capital
    validated_data[i] = upper_data
    
    # if i < len(data_list):
    #     data_list[i]['submitted'] = True
    # upper_data = {k: v for k,v in updated_data.items()} # v.upper untuk capital
    # validated_data[i] = upper_data

    if i==len(data_list)-1:
        response = [validated_data[k] for k in sorted(validated_data.keys())]
        return jsonify(response)
    else:
        return redirect(url_for('viewer', i=i+1))

    
# Route untuk mendapatkan daftar file di dalam path (Folder atau Single File)
@app.route("/get_files_in_path", methods=["POST"])
def get_files_in_path():
    path = request.form.get('path')
    if not path or not os.path.exists(path):
        return jsonify({"error": "Path tidak ditemukan"}), 404
    
    files_to_process = []
    
    if os.path.isfile(path):
        if path.lower().endswith(('.pdf', '.png', 'jpeg', 'jpg')):
            files_to_process.append(path)
    elif os.path.isdir(path):
        all_files = os.listdir(path)

        def extract_number(filename):
            match = re.search(r'\d+', filename)
            return int(match.group()) if match else 0
        
        all_files = sorted(all_files, key=extract_number)
        
        for f in all_files:
            if f.lower().endswith(('.pdf', '.png', 'jpeg', 'jpg')):
                files_to_process.append(os.path.join(path, f))
                
    if not files_to_process:
        return jsonify({"error": "Tidak ada file PDF atau PNG/JPG yang valid"}), 400
        
    return jsonify({"files": files_to_process})

# Route untuk memproses satu file berdasarkan path lokal
@app.route("/process_single_path", methods=["POST"])
def process_single_path():
    global data_list
    file_path = request.form.get('file_path')
    add_prompt = request.form.get('prompt')

    file_ext = file_path.lower().split('.')[-1]

    try:
        if file_path.lower().endswith(('.pdf', 'png', 'jpeg', 'jpg')):
            with open(file_path, 'rb') as f:
                file_bytes = f.read()
            data = extract_info(file_bytes, file_ext, add_prompt)
        else: #kalo tipe datanya bisa beberapa
            None
        data_list.append({
            'file_name': os.path.basename(file_path),
            'data': data
        })
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/viewer/<int:i>")
def viewer(i):
    if len(data_list)==0:
        return "Document tidak terdeteksi"
    if i >= len(data_list):
        return "Dataset selesai"
    if i in validated_data:
        data_list[i]['data']=validated_data[i]
    img = data_list[i]
    return render_template(
        "viewer.html",
        img=img,
        i=i,
        total=len(data_list),
        data_list = data_list,
        labels=labels
    )
    
@app.route("/reset_folder", methods=["POST"])
def reset_folder():
    global data_list, validated_data
    validated_data = {}
    data_list = []
    return jsonify({"status": "cleared"})

if __name__ == "__main__":
    # app.run(host='0.0.0.0', debug=True, port=8080)
    app.run(debug=True)