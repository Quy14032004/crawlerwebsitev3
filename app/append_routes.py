import codecs

code = """

@main_bp.route('/scrape-meanwell', methods=['POST'])
@progress_tracker(name="Cào dữ liệu MeanWell", total_steps=100, verbose=True)
def scrape_meanwell(progress):
    try:
        import traceback
        from flask import request, current_app, redirect, url_for, render_template, flash, send_file, jsonify
        import os
        import tempfile
        import zipfile
        from app.crawlerMeanWel import scrape_meanwell_category
        import pandas as pd
        from datetime import datetime
        
        urls_text = request.form.get('urls', '')
        file = request.files.get('link_file')
        
        urls = []
        if file and file.filename:
            content = file.read().decode('utf-8')
            urls.extend([u.strip() for u in content.splitlines() if u.strip()])
            
        if urls_text:
            urls.extend([u.strip() for u in urls_text.splitlines() if u.strip()])
            
        if not urls:
            progress.error("Không có URL nào được cung cấp.")
            flash('Không có URL nào được cung cấp.', 'error')
            return redirect(url_for('main.index'))
            
        progress.update(5, "Khởi tạo thư mục kết quả")
        
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        result_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], f'output_meanwell_{timestamp}')
        os.makedirs(result_dir, exist_ok=True)
        
        all_data = []
        for i, url in enumerate(urls):
            progress.update(10 + int((i/len(urls))*80), f"Đang cào dữ liệu từ URL {i+1}/{len(urls)}")
            def prog_cb(pct, msg):
                pass
            data = scrape_meanwell_category(url, result_dir, progress_callback=prog_cb)
            if data:
                all_data.extend(data)
                
        if all_data:
            progress.update(90, "Đang xuất file Excel")
            excel_path = os.path.join(result_dir, f"MeanWell_Data_{timestamp}.xlsx")
            df = pd.DataFrame(all_data)
            with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Dữ liệu Sản phẩm')
                sheet = writer.sheets['Dữ liệu Sản phẩm']
                for idx, col in enumerate(df.columns, 1):
                    import openpyxl
                    column_letter = openpyxl.utils.get_column_letter(idx)
                    if col in ['Link gốc', 'Đường dẫn ảnh']:
                        sheet.column_dimensions[column_letter].width = 50
                    elif col == 'Thông số kỹ thuật (HTML)':
                        sheet.column_dimensions[column_letter].width = 100
                    else:
                        sheet.column_dimensions[column_letter].width = 25
                        
            progress.update(95, "Đang tạo file ZIP kết quả")
            zip_filename = f"MeanWell_Result_{timestamp}.zip"
            zip_path = os.path.join(current_app.config['UPLOAD_FOLDER'], zip_filename)
            
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(result_dir):
                    for f in files:
                        file_path = os.path.join(root, f)
                        arcname = os.path.relpath(file_path, result_dir)
                        zipf.write(file_path, arcname)
                        
            download_link = url_for('main.download_meanwell_result', filename=zip_filename)
            flash(f'Đã cào thành công {len(all_data)} sản phẩm. Nhấn nút tải về để nhận file ZIP.', 'success')
            progress.complete("success", "Hoàn thành!", {"Sản phẩm xử lý": len(all_data)})
            
            # Using the same template logic as COGNEX
            return render_template('result_success.html', 
                title='Kết quả cào MeanWell',
                message=f'Đã cào thành công {len(all_data)} sản phẩm.',
                download_link=download_link,
                result_dir=result_dir,
                products_count=len(all_data),
                zip_filename=zip_filename,
                stats={"Sản phẩm đã xử lý": len(all_data)},
                crawler_type="MeanWell"
            )
        else:
            progress.error("Không thu thập được sản phẩm nào.")
            flash('Không tìm thấy sản phẩm nào từ các URL đã nhập.', 'warning')
            return redirect(url_for('main.index'))
            
    except Exception as e:
        error_msg = f"Lỗi khi cào dữ liệu MeanWell: {str(e)}"
        print(error_msg)
        progress.error(error_msg)
        flash(error_msg, 'error')
        return redirect(url_for('main.index'))

@main_bp.route('/download-meanwell-result/<path:filename>')
def download_meanwell_result(filename):
    import os
    from flask import current_app, send_file, jsonify
    try:
        file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        if os.path.exists(file_path):
            return send_file(file_path, as_attachment=True)
        return jsonify({'error': 'File không tồn tại'}), 404
    except Exception as e:
        return jsonify({'error': f'Lỗi tải xuống: {str(e)}'}), 500
"""

with codecs.open('routes.py', 'a', encoding='utf-8') as f:
    f.write(code)
print("Appended routes")




