import codecs

content = codecs.open('templates/index.html', 'r', encoding='utf-8').read()

meanwell_tab_content = """
                <!-- Tab cào dữ liệu MeanWell -->
                <div class="tab-pane fade" id="meanwell-crawler" role="tabpanel" aria-labelledby="meanwell-crawler-tab">
                    <div class="form-container">
                        <h4 class="mb-3">Cào dữ liệu MeanWell</h4>
                        <div class="data-format">
                            <h5>Chức năng này sẽ:</h5>
                            <ol>
                                <li>Tải file PDF thông số (Catalog) tự động dựa vào mã sản phẩm.</li>
                                <li>Phân tích ảnh tốt nhất từ bìa Catalog và chuyển sang định dạng WebP 800x800 chuẩn SEO.</li>
                                <li>Trích xuất các dòng thiết bị chi tiết (VD: RS-15-3.3, RS-15-5...).</li>
                                <li>Bóc tách bảng SPECIFICATION và xử lý thông số bị thiếu/gộp ô chuẩn xác.</li>
                                <li>Đóng gói 1 file Excel + toàn bộ ảnh vào 1 file ZIP để tải về.</li>
                            </ol>
                        </div>

                        <div class="steps mt-3">
                            <div class="step">
                                <p>Tải lên file <strong>.txt</strong> chứa danh sách URL danh mục (hoặc dán trực tiếp vào ô chữ).</p>
                            </div>
                            <div class="step">
                                <p>Ví dụ URL: <code>https://www.meanwell.com/webapp/product/search.aspx?prod=RS-15</code></p>
                            </div>
                        </div>

                        <form action="/scrape-meanwell" method="post" enctype="multipart/form-data" id="meanwell-form">
                            <div class="mb-3">
                                <label for="meanwell_urls" class="form-label">Dán link (mỗi link 1 dòng):</label>
                                <textarea class="form-control" id="meanwell_urls" name="urls" rows="4" placeholder="https://www.meanwell.com/webapp/product/search.aspx?prod=RS-15"></textarea>
                            </div>
                            <label for="meanwell_link_file" class="file-input-label mt-2">
                                <i class="bi bi-file-earmark-text fs-3"></i>
                                <div class="mt-2">Hoặc tải file .txt chứa danh sách link</div>
                                <input type="file" id="meanwell_link_file" name="link_file" class="d-none" accept=".txt" onchange="updateFileName(this, 'meanwell_file_name')">
                            </label>
                            <div id="meanwell_file_name" class="mb-3 text-muted"></div>
                            
                            <button type="submit" class="btn btn-primary w-100 mt-2">
                                <i class="bi bi-lightning-charge me-2"></i>Bắt đầu cào dữ liệu MeanWell
                            </button>
                        </form>
                    </div>
                </div>
"""

idx = content.find('id="cognex-crawler"')
if idx != -1:
    # find the previous `<div class="tab-pane`
    insert_idx = content.rfind('<div class="tab-pane', 0, idx)
    if insert_idx != -1:
        new_content = content[:insert_idx] + meanwell_tab_content + '\n' + content[insert_idx:]
        with codecs.open('templates/index.html', 'w', encoding='utf-8') as f:
            f.write(new_content)
        print("Inserted successfully before cognex-crawler.")
    else:
        print("Found cognex-crawler but not the div start.")
else:
    print("Could not find cognex-crawler.")
