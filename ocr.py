from flask import Flask, render_template, request, redirect, url_for, jsonify
import os
import fitz
import base64
from dotenv import load_dotenv
from pydantic import BaseModel, RootModel
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
Anda adalah AI Vision Document Extractor untuk sistem enterprise.

Tugas Anda membaca dokumen gambar / PDF yang diunggah user, memahami isi dokumen, lalu mengekstrak data terstruktur agar dapat otomatis mengisi form page.

### ATURAN UMUM:
1. Fokus hanya pada teks yang terlihat pada dokumen.
2. Jangan mengarang data, kecuali jika ada instruksi khusus dari user untuk mengisi field tertentu dengan nilai spesifik.
3. Jika field tidak ditemukan, isi null.
4. Nilai nominal wajib angka tanpa simbol mata uang.
5. Hilangkan spasi berlebih.

### ATURAN KHUSUS:
*item_price adalah harga satuan dari sebuah item
*subtotal dari Item adalah item_price * quantity
"""

data_list = []
validated_data = {}

@app.route("/")
def index():
    return render_template("index.html", text=None, path=None)

class Item(BaseModel):
      item_name: str
      item_price: int
      quantity: int
      subtotal: int

class Struk(BaseModel):
      store_name: str
      transaction_date: str
      receipt_no: str
      item: List[Item]
      description: str
      total_paid: int

class Output(RootModel[List[Struk]]):
      pass

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

def extract_info(input, additional_prompt=""):
    client = OpenAI(
        base_url=BASE_URL,
        api_key=API_KEY
    )
    content=[]
    final_prompt = PROMPT
    if(additional_prompt):
        print(f'##############################################################################################################\n{additional_prompt}\n##############################################################################################################')
        final_prompt += "\n\n### INSTRUKSI TAMBAHAN (PRIORITAS TERTINGGI - TIMPA ATURAN LAIN JIKA BERTENTANGAN):\n" + additional_prompt
    content.append({"type": "text", "text": "Extract dokumen berikut"})
    try:
        if isinstance(input, bytes):
            doc = fitz.open(stream=input, filetype="pdf")
        elif isinstance(input, str):
            if not os.path.exists(input):
                raise FileNotFoundError(f"File tidak ditemukan: {input}")
            doc = fitz.open(input)
        else:
            raise ValueError("Input harus berupa file / path")
    finally:
        if doc:
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
                response_format=Output,
            )
            data = json.loads(response.choices[0].message.content)
            

            print(response.choices[0].message.content)
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
    
    if file_ext == 'pdf':
        data = extract_info(file_bytes, add_prompt)
    else:
        return jsonify({"error": "Unsupported file type"}), 400

    for item in data:
        data_list.append({
                'file_name': os.path.basename(file.filename),
                'data': item
            })
    
    return jsonify({"status": "success"})

@app.route("/submit", methods=["POST"])
def submit():
    global data_list, validated_data

    # form_data = request.form.to_dict()
    # i = int(form_data.pop("index"))
##################
    i = int(request.form.get('index'))
    
    store_name = request.form.get('store_name')
    transaction_date = request.form.get('transaction_date')
    receipt_no = request.form.get('receipt_no')
    description = request.form.get('description')
    total_paid = request.form.get('total_paid')
    
    names = request.form.getlist('item_name[]')
    prices = request.form.getlist('item_price[]')
    qtys = request.form.getlist('item_qty[]')
    subtotals = request.form.getlist('item_subtotal[]')
    
    # 3. Rekonstruksi kembali menjadi format JSON asal kamu
    reconstructed_items = []
    for idx in range(len(names)):
        reconstructed_items.append({
            "item_name": names[idx],
            "item_price": int(prices[idx]) if prices[idx].isdigit() else 0,
            "quantity": int(qtys[idx]) if qtys[idx].isdigit() else 0,
            "subtotal": int(subtotals[idx]) if subtotals[idx].isdigit() else 0
        })
        
    # 4. Satukan ke struktur data utama
    updated_data = {
        "store_name": store_name,
        "transaction_date": transaction_date,
        "receipt_no": receipt_no,
        "item": reconstructed_items,
        "description": description,
        "total_paid": int(total_paid) if total_paid.isdigit() else 0
    }
##################
    if i < len(data_list):
        data_list[i]['submitted'] = True
    upper_data = {k: v for k,v in updated_data.items()} # v.upper untuk capital
    validated_data[i] = upper_data

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
        if path.lower().endswith('.pdf'):
            files_to_process.append(path)
    elif os.path.isdir(path):
        all_files = os.listdir(path)

        def extract_number(filename):
            match = re.search(r'\d+', filename)
            return int(match.group()) if match else 0
        
        all_files = sorted(all_files, key=extract_number)
        
        for f in all_files:
            if f.lower().endswith('.pdf'):
                files_to_process.append(os.path.join(path, f))
                
    if not files_to_process:
        return jsonify({"error": "Tidak ada file PDF yang valid"}), 400
        
    return jsonify({"files": files_to_process})

# Route untuk memproses satu file berdasarkan path lokal
@app.route("/process_single_path", methods=["POST"])
def process_single_path():
    global data_list
    file_path = request.form.get('file_path')
    add_prompt = request.form.get('prompt')

    try:
        if file_path.lower().endswith('.pdf'):
            with open(file_path, 'rb') as f:
                file_bytes = f.read()
            data = extract_info(file_bytes, add_prompt)
        else: #kalo tipe datanya bisa beberapa
            None
        for item in data:
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