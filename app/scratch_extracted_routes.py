@main_bp.route('/crawl-codienhaiau', methods=['GET', 'POST'])
@progress_tracker(name="Cào dữ liệu Cơ Điện Hải Âu", total_steps=100, verbose=True)
def crawl_codienhaiau(progress: TerminalProgressBar):
    if request.method == 'GET':
        # Redirect GET requests to main page
        return redirect(url_for('main.index'))
    
    try:
        progress.update(5, "Đang kiểm tra thông tin đầu vào")
        
        # Lấy thông tin từ form
        category_urls = request.form.get('category_urls', '').strip()
        if not category_urls:
            progress.error('Vui lòng nhập ít nhất một URL danh mục hoặc sản phẩm.')
            flash('Vui lòng nhập ít nhất một URL danh mục hoặc sản phẩm.', 'error')
            return redirect(url_for('main.index'))
        
        # Lấy tham số tùy chọn
        max_workers = int(request.form.get('max_workers', 8))
        max_retries = int(request.form.get('max_retries', 3))
        
        # Giới hạn giá trị hợp lệ
        max_workers = min(max(1, max_workers), 16)  # Từ 1-16 luồng
        max_retries = min(max(1, max_retries), 5)   # Từ 1-5 lần thử lại
        
        progress.update(10, "Đang chuẩn bị crawler", f"Max workers: {max_workers}, Max retries: {max_retries}")
        
        # Log thông tin
        print(f"🚀 CDHA: {max_workers} luồng, {max_retries} retry")
        
        url_list = [u.strip() for u in category_urls.splitlines() if u.strip()]
        
        progress.update(15, f"Đã phân tích {len(url_list)} URLs", "Đang khởi tạo CDHA crawler")
        
        # Tạo progress bar con cho crawler
        crawler_progress = create_child_progress(progress, "CDHA Product Crawler", 70)
        
        # Import CDHA crawler
        from app.crawlerCDHA import CDHACrawler
        
        # Tạo thư mục output
        output_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'output_cdha')
        os.makedirs(output_folder, exist_ok=True)
        
        # Khởi tạo crawler
        crawler = CDHACrawler(
            output_root=output_folder,
            max_workers=max_workers,
            max_retries=max_retries
        )
        
        progress.update(20, "Bắt đầu cào dữ liệu", "Đang xử lý các URL...")
        
        # Chạy crawler
        start_time = time.time()
        products, result_dir = crawler.crawl_products(url_list)
        duration = time.time() - start_time
        
        progress.update(90, "Hoàn thành cào dữ liệu", f"Đã xử lý {len(products)} sản phẩm")
        
        # Tạo thông báo kết quả
        if products:
            progress.update(95, "Đang tạo báo cáo kết quả")
            
            # Tạo link download
            zip_files = [f for f in os.listdir(result_dir) if f.endswith('.zip')]
            if zip_files:
                download_link = url_for('main.download_cdha_result', filename=zip_files[0])
                flash(f'Cào dữ liệu thành công! Đã xử lý {len(products)} sản phẩm từ Cơ Điện Hải Âu.', 'success')
                progress.update(100, "Hoàn thành!", f"Kết quả: {len(products)} sản phẩm")
                
                # Tạo stats cho template
                stats = {
                    "Sản phẩm đã xử lý": len(products),
                    "Thời gian thực hiện": f"{duration:.2f} giây",
                    "Số luồng sử dụng": max_workers,
                    "Số lần retry": max_retries
                }
                
                return render_template('crawler_result.html', 
                                     success=True,
                                     message=f'Đã cào thành công {len(products)} sản phẩm từ Cơ Điện Hải Âu',
                                     download_link=download_link,
                                     result_dir=result_dir,
                                     products_count=len(products),
                                     zip_filename=zip_files[0],
                                     stats=stats,
                                     crawler_type="Cơ Điện Hải Âu")
            else:
                flash('Cào dữ liệu thành công nhưng không tìm thấy file kết quả.', 'warning')
        else:
            flash('Không tìm thấy sản phẩm nào từ các URL đã nhập.', 'warning')
        
        progress.update(100, "Hoàn thành")
        return redirect(url_for('main.index'))
        
    except Exception as e:
        error_msg = f"Lỗi khi cào dữ liệu Cơ Điện Hải Âu: {str(e)}"
        print(error_msg)
        print(traceback.format_exc())
        progress.error(error_msg)
        flash(error_msg, 'error')
        return redirect(url_for('main.index'))


@main_bp.route('/crawl-lsa', methods=['GET', 'POST'])
@progress_tracker(name="Cào dữ liệu LS Automation", total_steps=100, verbose=True)
def crawl_lsa(progress: TerminalProgressBar):
    if request.method == 'GET':
        # Redirect GET requests to main page
        return redirect(url_for('main.index'))
    
    try:
        progress.update(5, "Đang kiểm tra thông tin đầu vào")
        
        # Lấy thông tin từ form
        category_urls = request.form.get('category_urls', '').strip()
        if not category_urls:
            progress.error('Vui lòng nhập ít nhất một URL danh mục hoặc sản phẩm.')
            flash('Vui lòng nhập ít nhất một URL danh mục hoặc sản phẩm.', 'error')
            return redirect(url_for('main.index'))
        
        # Lấy tham số tùy chọn
        max_workers = int(request.form.get('max_workers', 8))
        max_retries = int(request.form.get('max_retries', 3))
        
        # Giới hạn giá trị hợp lệ
        max_workers = min(max(1, max_workers), 16)  # Từ 1-16 luồng
        max_retries = min(max(1, max_retries), 5)   # Từ 1-5 lần thử lại
        
        progress.update(10, "Đang chuẩn bị crawler", f"Max workers: {max_workers}, Max retries: {max_retries}")
        
        # Log thông tin
        print(f"🚀 LSA: {max_workers} luồng, {max_retries} retry")
        
        url_list = [u.strip() for u in category_urls.splitlines() if u.strip()]
        
        progress.update(15, f"Đã phân tích {len(url_list)} URLs", "Đang khởi tạo LSA crawler")
        
        # Tạo progress bar con cho crawler
        crawler_progress = create_child_progress(progress, "LSA Product Crawler", 70)
        
        # Import LSA crawler
        from app.crawlerLSA import LSACrawler
        
        # Tạo thư mục output
        output_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'output_lsa')
        os.makedirs(output_folder, exist_ok=True)
        
        # Khởi tạo crawler
        crawler = LSACrawler(
            output_root=output_folder,
            max_workers=max_workers,
            max_retries=max_retries
        )
        
        progress.update(20, "Bắt đầu cào dữ liệu", "Đang xử lý các URL...")
        
        # Chạy crawler
        start_time = time.time()
        products, result_dir = crawler.crawl_products(url_list)
        duration = time.time() - start_time
        
        progress.update(90, "Hoàn thành cào dữ liệu", f"Đã xử lý {len(products)} sản phẩm")
        
        # Tạo thông báo kết quả
        if products:
            progress.update(95, "Đang tạo báo cáo kết quả")
            
            # Tạo link download
            zip_files = [f for f in os.listdir(result_dir) if f.endswith('.zip')]
            if zip_files:
                download_link = url_for('main.download_lsa_result', filename=zip_files[0])
                flash(f'Cào dữ liệu thành công! Đã xử lý {len(products)} sản phẩm từ LS Automation.', 'success')
                progress.update(100, "Hoàn thành!", f"Kết quả: {len(products)} sản phẩm")
                
                # Tạo stats cho template
                stats = {
                    "Sản phẩm đã xử lý": len(products),
                    "Thời gian thực hiện": f"{duration:.2f} giây",
                    "Số luồng sử dụng": max_workers,
                    "Số lần retry": max_retries
                }
                
                return render_template('crawler_result.html', 
                                     success=True,
                                     message=f'Đã cào thành công {len(products)} sản phẩm từ LS Automation',
                                     download_link=download_link,
                                     result_dir=result_dir,
                                     products_count=len(products),
                                     zip_filename=zip_files[0],
                                     stats=stats,
                                     crawler_type="LS Automation")
            else:
                flash('Cào dữ liệu thành công nhưng không tìm thấy file kết quả.', 'warning')
        else:
            flash('Không tìm thấy sản phẩm nào từ các URL đã nhập.', 'warning')
        
        progress.update(100, "Hoàn thành")
        return redirect(url_for('main.index'))
        
    except Exception as e:
        error_msg = f"Lỗi khi cào dữ liệu LS Automation: {str(e)}"
        print(error_msg)
        print(traceback.format_exc())
        progress.error(error_msg)
        flash(error_msg, 'error')
        return redirect(url_for('main.index'))


@main_bp.route('/baa-qlight-crawler')
def baa_qlight_crawler():
    """
    Giao diện BAA Qlight Crawler với 3 bước: chọn danh mục → chọn series → cào dữ liệu
    """
    return render_template('baa_qlight_crawler.html')


@main_bp.route('/crawl-sunje', methods=['GET', 'POST'])
@progress_tracker(name="Cào dữ liệu SUNJE", total_steps=100, verbose=True)
def crawl_sunje(progress: TerminalProgressBar):
    if request.method == 'GET':
        # Redirect GET requests to main page
        return redirect(url_for('main.index'))
    
    try:
        progress.update(5, "Đang kiểm tra thông tin đầu vào")
        
        # Lấy thông tin từ form
        category_url = request.form.get('category_url', '').strip()
        if not category_url:
            progress.error('Vui lòng nhập URL danh mục.')
            flash('Vui lòng nhập URL danh mục.', 'error')
            return redirect(url_for('main.index'))
        
        # Lấy tham số tùy chọn
        max_workers = int(request.form.get('max_workers', 8))
        max_retries = int(request.form.get('max_retries', 3))
        
        # Giới hạn giá trị hợp lệ
        max_workers = min(max(1, max_workers), 16)  # Từ 1-16 luồng
        max_retries = min(max(1, max_retries), 5)   # Từ 1-5 lần thử lại
        
        progress.update(10, "Đang chuẩn bị crawler", f"Max workers: {max_workers}, Max retries: {max_retries}")
        
        # Log thông tin
        print(f"🚀 SUNJE: {max_workers} luồng, {max_retries} retry")
        
        progress.update(15, "Đang khởi tạo SUNJE crawler")
        
        # Import SUNJE crawler
        from app.crawlerSUNJE import SUNJECrawler
        
        # Tạo thư mục output
        output_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'output_sunje')
        os.makedirs(output_folder, exist_ok=True)
        
        # Lấy tùy chọn dịch thông số kỹ thuật
        translate_specs = request.form.get('translate_specs', 'false').lower() == 'true'
        
        # Khởi tạo crawler
        crawler = SUNJECrawler(
            output_root=output_folder,
            max_workers=max_workers,
            max_retries=max_retries,
            translate_specs=translate_specs
        )
        
        progress.update(20, "Bắt đầu cào dữ liệu", f"Đang xử lý URL: {category_url}")
        
        # Chạy crawler
        start_time = time.time()
        products, result_dir = crawler.crawl_products(category_url)
        duration = time.time() - start_time
        
        progress.update(90, "Hoàn thành cào dữ liệu", f"Đã xử lý {len(products)} sản phẩm")
        
        # Tạo thông báo kết quả
        if products:
            progress.update(95, "Đang tạo báo cáo kết quả")
            
            # Tìm file ZIP trong result_dir
            zip_files = [f for f in os.listdir(result_dir) if f.endswith('.zip') and 'SUNJE' in f]
            
            if zip_files:
                zip_filename = zip_files[0]
                zip_file_path = os.path.join(result_dir, zip_filename)
                
                # Copy file ZIP lên thư mục output_sunje để dễ download
                final_zip_path = os.path.join(output_folder, zip_filename)
                if os.path.exists(zip_file_path):
                    if zip_file_path != final_zip_path:
                        shutil.copy2(zip_file_path, final_zip_path)
                    
                    download_link = url_for('main.download_sunje_result', filename=zip_filename)
                    flash(f'Cào dữ liệu thành công! Đã xử lý {len(products)} sản phẩm từ SUNJE.', 'success')
                    progress.update(100, "Hoàn thành!", f"Kết quả: {len(products)} sản phẩm")
                    
                    # Tạo stats cho template
                    stats = {
                        "Sản phẩm đã xử lý": len(products),
                        "Thời gian thực hiện": f"{duration:.2f} giây",
                        "Số luồng sử dụng": max_workers,
                        "Số lần retry": max_retries
                    }
                    
                    return render_template('crawler_result.html', 
                                         success=True,
                                         message=f'Đã cào thành công {len(products)} sản phẩm từ SUNJE',
                                         download_link=download_link,
                                         result_dir=result_dir,
                                         products_count=len(products),
                                         zip_filename=zip_filename,
                                         stats=stats,
                                         crawler_type="SUNJE")
                else:
                    flash('Cào dữ liệu thành công nhưng không tìm thấy file ZIP.', 'warning')
            else:
                flash('Cào dữ liệu thành công nhưng không tìm thấy file kết quả.', 'warning')
        else:
            flash('Không tìm thấy sản phẩm nào từ URL đã nhập.', 'warning')
        
        progress.update(100, "Hoàn thành")
        return redirect(url_for('main.index'))
        
    except Exception as e:
        error_msg = f"Lỗi khi cào dữ liệu SUNJE: {str(e)}"
        print(error_msg)
        print(traceback.format_exc())
        progress.error(error_msg)
        flash(error_msg, 'error')
        return redirect(url_for('main.index'))


@main_bp.route('/crawl-panasonic', methods=['GET', 'POST'])
@progress_tracker(name="Cào dữ liệu Panasonic", total_steps=100, verbose=True)
def crawl_panasonic(progress: TerminalProgressBar):
    if request.method == 'GET':
        # Redirect GET requests to main page
        return redirect(url_for('main.index'))
    
    try:
        progress.update(5, "Đang kiểm tra thông tin đầu vào")
        
        # Lấy thông tin từ form
        category_urls = request.form.get('category_urls', '').strip()
        if not category_urls:
            progress.error('Vui lòng nhập ít nhất một URL danh mục.')
            flash('Vui lòng nhập ít nhất một URL danh mục.', 'error')
            return redirect(url_for('main.index'))
        
        # Lấy tham số tùy chọn
        max_workers = int(request.form.get('max_workers', 8))
        max_retries = int(request.form.get('max_retries', 3))
        
        # Giới hạn giá trị hợp lệ
        max_workers = min(max(1, max_workers), 16)  # Từ 1-16 luồng
        max_retries = min(max(1, max_retries), 5)   # Từ 1-5 lần thử lại
        
        progress.update(10, "Đang chuẩn bị crawler", f"Max workers: {max_workers}, Max retries: {max_retries}")
        
        # Log thông tin
        print(f"🚀 PANASONIC: {max_workers} luồng, {max_retries} retry")
        
        progress.update(15, "Đang khởi tạo Panasonic crawler")
        
        # Import Panasonic crawler
        from app.crawlerPANASONIC import PanasonicCrawler
        
        # Tạo thư mục output
        output_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'output_panasonic')
        os.makedirs(output_folder, exist_ok=True)
        
        # Khởi tạo crawler
        crawler = PanasonicCrawler(
            output_root=output_folder,
            max_workers=max_workers,
            max_retries=max_retries
        )
        
        progress.update(20, "Bắt đầu cào dữ liệu", "Đang xử lý các URL...")
        
        # Phân tích URL (mỗi URL một dòng)
        url_list = [url.strip() for url in category_urls.split('\n') if url.strip()]
        
        # Chạy crawler
        start_time = time.time()
        products, result_dir = crawler.crawl_products(url_list)
        duration = time.time() - start_time
        
        progress.update(90, "Hoàn thành cào dữ liệu", f"Đã xử lý {len(products)} sản phẩm")
        
        # Tạo thông báo kết quả
        if products:
            progress.update(95, "Đang tạo báo cáo kết quả")
            
            # Tìm file ZIP trong result_dir
            zip_files = [f for f in os.listdir(result_dir) if f.endswith('.zip') and 'PANASONIC' in f]
            
            if zip_files:
                zip_filename = zip_files[0]
                zip_file_path = os.path.join(result_dir, zip_filename)
                
                # Copy file ZIP lên thư mục output_panasonic để dễ download
                final_zip_path = os.path.join(output_folder, zip_filename)
                if os.path.exists(zip_file_path):
                    if zip_file_path != final_zip_path:
                        shutil.copy2(zip_file_path, final_zip_path)
                    
                    download_link = url_for('main.download_panasonic_result', filename=zip_filename)
                    flash(f'Cào dữ liệu thành công! Đã xử lý {len(products)} sản phẩm từ Panasonic.', 'success')
                    progress.update(100, "Hoàn thành!", f"Kết quả: {len(products)} sản phẩm")
                    
                    # Tạo stats cho template
                    stats = {
                        "Sản phẩm đã xử lý": len(products),
                        "Thời gian thực hiện": f"{duration:.2f} giây",
                        "Số luồng sử dụng": max_workers,
                        "Số lần retry": max_retries
                    }
                    
                    return render_template('crawler_result.html', 
                                         success=True,
                                         message=f'Đã cào thành công {len(products)} sản phẩm từ Panasonic',
                                         download_link=download_link,
                                         result_dir=result_dir,
                                         products_count=len(products),
                                         zip_filename=zip_filename,
                                         stats=stats,
                                         crawler_type="Panasonic")
                else:
                    flash('Cào dữ liệu thành công nhưng không tìm thấy file ZIP.', 'warning')
            else:
                flash('Cào dữ liệu thành công nhưng không tìm thấy file kết quả.', 'warning')
        else:
            flash('Không tìm thấy sản phẩm nào từ các URL đã nhập.', 'warning')
        
        progress.update(100, "Hoàn thành")
        return redirect(url_for('main.index'))
        
    except Exception as e:
        error_msg = f"Lỗi khi cào dữ liệu Panasonic: {str(e)}"
        print(error_msg)
        print(traceback.format_exc())
        progress.error(error_msg)
        flash(error_msg, 'error')
        return redirect(url_for('main.index'))


@main_bp.route('/crawl-woonyoung', methods=['POST'])
@progress_tracker(name="Cào dữ liệu WOONYOUNG", total_steps=100, verbose=True)
def crawl_woonyoung(progress: TerminalProgressBar):
    """Cào dữ liệu từ website woonyoung.com"""
    try:
        progress.update(5, "Đang kiểm tra thông tin đầu vào")
        
        # Lấy URL danh mục từ form
        category_url = request.form.get('category_url', '').strip()
        
        if not category_url:
            progress.error('Vui lòng nhập URL danh mục.')
            flash('Vui lòng nhập URL danh mục.', 'error')
            return redirect(url_for('main.index'))
        
        # Kiểm tra URL phải là trang list chứ không phải trang view
        if 's0201_view.php' in category_url:
            error_msg = 'Vui lòng nhập URL trang DANH MỤC (s0201_list.php), không phải trang chi tiết sản phẩm (s0201_view.php)'
            progress.error(error_msg)
            flash(error_msg, 'error')
            return redirect(url_for('main.index'))
        
        # Cho phép cả URL danh mục cha (s0201_mid.php) và danh mục con (s0201_list.php)
        if ('s0201_list.php' not in category_url) and ('s0201_mid.php' not in category_url):
            error_msg = 'URL không đúng định dạng. Vui lòng nhập URL WOONYOUNG chứa s0201_list.php hoặc s0201_mid.php'
            progress.error(error_msg)
            flash(error_msg, 'error')
            return redirect(url_for('main.index'))
        
        # Lấy tham số tùy chọn
        max_workers = int(request.form.get('max_workers', 8))
        max_retries = int(request.form.get('max_retries', 3))
        
        # Giới hạn giá trị hợp lệ
        max_workers = min(max(1, max_workers), 16)  # Từ 1-16 luồng
        max_retries = min(max(1, max_retries), 5)   # Từ 1-5 lần thử lại
        
        progress.update(10, "Đang chuẩn bị crawler", f"Max workers: {max_workers}, Max retries: {max_retries}")
        
        # Log thông tin
        print(f"🚀 WOONYOUNG: {max_workers} luồng, {max_retries} retry")
        print(f"📥 URL nhận được: {category_url[:100]}...")
        
        progress.update(15, f"Đang xử lý URL danh mục", "Đang khởi tạo WOONYOUNG crawler")
        
        # Import WOONYOUNG crawler
        from app.crawlerWOONYOUNG import WOONYOUNGCrawler
        
        # Tạo thư mục output
        output_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'output_woonyoung')
        os.makedirs(output_folder, exist_ok=True)
        
        # Khởi tạo crawler
        crawler = WOONYOUNGCrawler(
            output_root=output_folder,
            max_workers=max_workers,
            max_retries=max_retries
        )
        
        progress.update(20, "Bắt đầu cào dữ liệu", "Đang xử lý URL danh mục...")

        # Cho phép nhập nhiều URL (mỗi dòng hoặc dấu phẩy)
        url_parts = [u.strip() for u in re.split(r"[\n,]", category_url) if u and u.strip()]

        if len(url_parts) <= 1:
            # Trường hợp 1 URL
            start_time = time.time()
            if 's0201_mid.php' in category_url:
                # URL cha: mở rộng và cào nhiều danh mục con
                multi_results = crawler.crawl_categories([category_url])
                # Gom sản phẩm từ tất cả danh mục con
                products_list = []
                result_dir = ''
                if multi_results:
                    # Lấy result_dir đầu tiên có dữ liệu
                    for r in multi_results:
                        if r.get('products'):
                            products_list.extend(r.get('products', []))
                        if not result_dir and r.get('result_dir'):
                            result_dir = r.get('result_dir')
            else:
                # URL con: xử lý đơn danh mục
                products_list, result_dir = crawler.crawl_category(category_url)
            duration = time.time() - start_time
            
            progress.update(90, "Hoàn thành cào dữ liệu", f"Đã xử lý {len(products_list)} sản phẩm")
            
            if products_list:
                progress.update(95, "Đang tạo báo cáo kết quả")
                zip_files = [f for f in os.listdir(result_dir) if f.endswith('.zip')]
                if zip_files:
                    download_link = url_for('main.download_woonyoung_result', filename=zip_files[0])
                    flash(f'Cào dữ liệu thành công! Đã xử lý {len(products_list)} sản phẩm từ WOONYOUNG.', 'success')
                    progress.update(100, "Hoàn thành!", f"Kết quả: {len(products_list)} sản phẩm")
                    stats = {
                        "Sản phẩm đã xử lý": len(products_list),
                        "Thời gian thực hiện": f"{duration:.2f} giây",
                        "Số luồng sử dụng": max_workers,
                        "Số lần retry": max_retries
                    }
                    return render_template('crawler_result.html', 
                                         success=True,
                                         message=f'Đã cào thành công {len(products_list)} sản phẩm từ WOONYOUNG',
                                         download_link=download_link,
                                         result_dir=result_dir,
                                         products_count=len(products_list),
                                         zip_filename=zip_files[0],
                                         stats=stats,
                                         crawler_type="WOONYOUNG")
                else:
                    flash('Cào dữ liệu thành công nhưng không tìm thấy file kết quả.', 'warning')
            else:
                flash('Không tìm thấy series nào từ URL đã nhập.', 'warning')
        else:
            # Trường hợp nhiều URL - dùng crawl_categories và hiển thị bảng tổng hợp
            start_time = time.time()
            multi_results = crawler.crawl_categories(url_parts)
            duration = time.time() - start_time

            success_count = sum(1 for r in multi_results if r.get('success'))
            zip_files = [os.path.basename(r.get('zip_file')) for r in multi_results if r.get('zip_file')]

            progress.update(90, "Hoàn thành cào nhiều danh mục", f"Thành công {success_count}/{len(url_parts)} danh mục")

            stats = {
                "Số danh mục nhập": len(url_parts),
                "Danh mục thành công": success_count,
                "Thời gian thực hiện": f"{duration:.2f} giây",
                "Số luồng sử dụng": max_workers,
                "Số lần retry": max_retries
            }

            progress.update(100, "Hoàn thành!", f"Đã xử lý {success_count}/{len(url_parts)} danh mục")

            return render_template(
                'crawler_result.html',
                success=(success_count > 0),
                message=f'Đã cào {success_count}/{len(url_parts)} danh mục WOONYOUNG',
                products_count=success_count,
                stats=stats,
                crawler_type="WOONYOUNG",
                multi_results=multi_results
            )
        
        progress.update(100, "Hoàn thành")
        return redirect(url_for('main.index'))
        
    except Exception as e:
        error_msg = f"Lỗi khi cào dữ liệu WOONYOUNG: {str(e)}"
        print(error_msg)
        print(traceback.format_exc())
        progress.error(error_msg)
        flash(error_msg, 'error')
        return redirect(url_for('main.index'))


@main_bp.route('/merge-excel', methods=['POST'])
@progress_tracker(name="Gộp file Excel", total_steps=100, verbose=True)
def merge_excel(progress: TerminalProgressBar):
    """
    Gộp nhiều file Excel thành một file tổng hợp
    """
    try:
        progress.update(5, "Kiểm tra file đầu vào")
        
        # Kiểm tra có file nào được upload không
        if 'excel_files' not in request.files:
            progress.error("Không tìm thấy file!")
            flash('Vui lòng chọn ít nhất một file Excel!', 'error')
            return redirect(url_for('main.index'))
        
        files = request.files.getlist('excel_files')
        
        # Lọc các file hợp lệ
        valid_files = []
        invalid_files = []
        
        for file in files:
            if file.filename == '':
                continue
                
            if not allowed_file(file.filename, ALLOWED_EXTENSIONS_EXCEL):
                invalid_files.append(file.filename)
                continue
                
            valid_files.append(file)
        
        progress.update(10, f"Kiểm tra hoàn tất", f"Files hợp lệ: {len(valid_files)}, Files không hợp lệ: {len(invalid_files)}")
        
        if not valid_files:
            progress.error("Không có file Excel hợp lệ!")
            flash('Không có file Excel hợp lệ nào được tìm thấy!', 'error')
            return redirect(url_for('main.index'))
        
        if len(valid_files) < 2:
            progress.error("Cần ít nhất 2 file để gộp!")
            flash('Cần ít nhất 2 file Excel để thực hiện gộp!', 'error')
            return redirect(url_for('main.index'))
        
        # Lưu các file tạm
        temp_files = []
        progress.update(15, "Đang lưu files tạm")
        
        for file in valid_files:
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
            temp_file_path = temp_file.name
            temp_file.close()
            
            file.save(temp_file_path)
            temp_files.append(temp_file_path)
        
        progress.update(20, f"Đã lưu {len(temp_files)} files tạm")
        
        # Tạo file output
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"merged_excel_{timestamp}.xlsx"
        output_path = os.path.join(current_app.config['UPLOAD_FOLDER'], output_filename)
        
        # Đảm bảo thư mục upload tồn tại
        os.makedirs(current_app.config['UPLOAD_FOLDER'], exist_ok=True)
        
        # Khởi tạo ExcelMerger và tiến hành gộp
        merger = ExcelMerger()
        
        def progress_callback(percent, message):
            """Callback để cập nhật progress cho user"""
            # Chuyển đổi percent từ merger (0-100) sang percent tổng (20-90)
            total_percent = 20 + (percent * 70 // 100)
            progress.update(total_percent, message)
            
            # Gửi qua socketio cho real-time update
            socketio.emit('progress_update', {
                'percent': total_percent,
                'message': message
            })
        
        progress.update(25, "Bắt đầu gộp files...")
        
        # Thực hiện gộp file
        result = merger.merge_excel_files(
            file_paths=temp_files, 
            output_path=output_path,
            progress_callback=progress_callback
        )
        
        # Xóa các file tạm
        for temp_file in temp_files:
            try:
                os.unlink(temp_file)
            except Exception as e:
                logger.warning(f"Không thể xóa file tạm {temp_file}: {str(e)}")
        
        if not result.get('success', False):
            progress.error(f"Lỗi: {result.get('error', 'Không xác định')}")
            flash(f'Lỗi khi gộp file: {result.get("error", "Không xác định")}', 'error')
            return redirect(url_for('main.index'))
        
        progress.update(95, "Hoàn tất gộp file")
        
        # Gửi thông báo thành công qua socketio
        socketio.emit('progress_update', {
            'percent': 100,
            'message': 'Gộp file Excel thành công!',
            'success': True,
            'download_url': url_for('main.download_file', filename=output_filename),
            'statistics': {
                'total_files': result['total_files'],
                'total_rows': result['total_rows'],
                'total_sheets': result['total_sheets'],
                'summary_rows': result['summary_rows']
            }
        })
        
        progress.complete("success", "Gộp file Excel thành công!", {
            "Files đã gộp": result['total_files'],
            "Tổng dòng": result['total_rows'],
            "Kết quả": "1 sheet tổng hợp",
            "File kết quả": output_filename
        })
        
        flash(f'Gộp file thành công! Đã gộp {result["total_files"]} files thành 1 sheet chứa {result["total_rows"]} dòng dữ liệu.', 'success')
        
        # Lưu download URL vào session để hiển thị trong template
        session['merge_excel_download_url'] = url_for('main.download_file', filename=output_filename)
        session['merge_excel_result_info'] = {
            'total_files': result['total_files'],
            'total_rows': result['total_rows'],
            'total_sheets': result['total_sheets'],
            'output_filename': output_filename,
            'product_analysis': result.get('product_analysis', {})
        }
        
        return redirect(url_for('main.index', _anchor='merge-excel-tab'))
    
    except Exception as e:
        # Cleanup temp files nếu có lỗi
        if 'temp_files' in locals():
            for temp_file in temp_files:
                try:
                    os.unlink(temp_file)
                except:
                    pass
        
        error_msg = f"Lỗi khi gộp file Excel: {str(e)}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        
        progress.error(error_msg)
        flash(error_msg, 'error')
        
        return redirect(url_for('main.index'))



@main_bp.route('/merge-excel-folder', methods=['POST'])
@progress_tracker(name="Gộp file Excel từ folder", total_steps=100, verbose=True)
def merge_excel_folder(progress: TerminalProgressBar):
    """
    Gộp file Excel từ folder structure hoặc ZIP file
    """
    try:
        progress.update(5, "Kiểm tra dữ liệu đầu vào")
        
        upload_type = request.form.get('upload_type', 'zip')  # 'zip' hoặc 'folder'
        
        if upload_type == 'zip':
            # Xử lý ZIP file upload
            if 'zip_file' not in request.files:
                progress.error("Không tìm thấy file ZIP!")
                flash('Vui lòng chọn một file ZIP!', 'error')
                return redirect(url_for('main.index'))
            
            zip_file = request.files['zip_file']
            
            if zip_file.filename == '':
                progress.error("Không có file ZIP nào được chọn!")
                flash('Vui lòng chọn một file ZIP!', 'error')
                return redirect(url_for('main.index'))
            
            if not allowed_file(zip_file.filename, ALLOWED_EXTENSIONS_ZIP):
                progress.error("File phải có định dạng .zip!")
                flash('Chỉ chấp nhận file .zip!', 'error')
                return redirect(url_for('main.index'))
            
            # Lưu ZIP file tạm
            temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
            temp_zip_path = temp_zip.name
            temp_zip.close()
            zip_file.save(temp_zip_path)
            
            progress.update(10, f"Đã lưu ZIP file: {zip_file.filename}")
            
            # Chuẩn bị tham số
            source_path = temp_zip_path
            merge_kwargs = {'zip_path': temp_zip_path}
            source_info = f"ZIP: {zip_file.filename}"
            
        elif upload_type == 'folder':
            # Xử lý folder upload (HTML5 webkitdirectory)
            if 'folder_files' not in request.files:
                progress.error("Không tìm thấy file từ folder!")
                flash('Vui lòng chọn một folder chứa file Excel!', 'error')
                return redirect(url_for('main.index'))
            
            files = request.files.getlist('folder_files')
            
            if not files or all(f.filename == '' for f in files):
                progress.error("Không có file nào được chọn từ folder!")
                flash('Vui lòng chọn folder chứa file Excel!', 'error')
                return redirect(url_for('main.index'))
            
            # Tạo thư mục tạm để lưu folder structure
            temp_folder = tempfile.mkdtemp(prefix="excel_folder_")
            
            # Lưu files với cấu trúc folder gốc
            saved_files = 0
            for file in files:
                if file.filename:
                    # Tạo đường dẫn đầy đủ trong temp folder
                    file_path = os.path.join(temp_folder, secure_filename(file.filename))
                    
                    # Tạo thư mục con nếu cần
                    os.makedirs(os.path.dirname(file_path), exist_ok=True)
                    
                    # Lưu file
                    file.save(file_path)
                    saved_files += 1
            
            progress.update(10, f"Đã lưu {saved_files} files từ folder")
            
            # Chuẩn bị tham số
            source_path = temp_folder
            merge_kwargs = {'folder_path': temp_folder}
            source_info = f"Folder: {saved_files} files"
            
        else:
            progress.error("Loại upload không hợp lệ!")
            flash('Loại upload không được hỗ trợ!', 'error')
            return redirect(url_for('main.index'))
        
        # Tạo file output
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"merged_excel_from_{upload_type}_{timestamp}.xlsx"
        output_path = os.path.join(current_app.config['UPLOAD_FOLDER'], output_filename)
        
        # Đảm bảo thư mục upload tồn tại
        os.makedirs(current_app.config['UPLOAD_FOLDER'], exist_ok=True)
        
        progress.update(15, f"Khởi tạo ExcelMerger cho {source_info}")
        
        # Khởi tạo ExcelMerger
        merger = ExcelMerger()
        
        def progress_callback(percent, message):
            """Callback để cập nhật progress cho user"""
            # Chuyển đổi percent từ merger (0-100) sang percent tổng (15-90)
            total_percent = 15 + (percent * 75 // 100)
            progress.update(total_percent, message)
            
            # Gửi qua socketio cho real-time update
            socketio.emit('progress_update', {
                'percent': total_percent,
                'message': message
            })
        
        # Thực hiện gộp file từ folder structure
        result = merger.merge_from_folder_structure(
            output_path=output_path,
            progress_callback=progress_callback,
            **merge_kwargs
        )
        
        # Cleanup temp files
        try:
            if upload_type == 'zip' and 'temp_zip_path' in locals():
                os.unlink(temp_zip_path)
            elif upload_type == 'folder' and 'temp_folder' in locals():
                import shutil
                shutil.rmtree(temp_folder)
        except Exception as e:
            logger.warning(f"Không thể xóa temp files: {str(e)}")
        
        if not result.get('success', False):
            progress.error(f"Lỗi: {result.get('error', 'Không xác định')}")
            flash(f'Lỗi khi gộp file từ {upload_type}: {result.get("error", "Không xác định")}', 'error')
            return redirect(url_for('main.index'))
        
        progress.update(95, "Hoàn tất gộp file từ folder")
        
        # Tạo thông báo thành công với thống kê folder
        folder_stats = result.get('folder_structure', {})
        success_message = f"""
        Gộp file từ {result['source_type']} thành công! 
        - Tổng files: {result['total_files']}
        - Tổng folders: {folder_stats.get('total_folders', 0)}
        - Dòng dữ liệu: {result['total_rows']}
        - Kết quả: 1 sheet chứa toàn bộ dữ liệu
        """
        
        # Gửi thông báo thành công qua socketio
        socketio.emit('progress_update', {
            'percent': 100,
            'message': 'Gộp file Excel từ folder thành công!',
            'success': True,
            'download_url': url_for('main.download_file', filename=output_filename),
            'statistics': {
                'source_type': result['source_type'],
                'total_files': result['total_files'],
                'total_rows': result['total_rows'],
                'total_sheets': result['total_sheets'],
                'folder_structure': folder_stats
            }
        })
        
        progress.complete("success", "Gộp file từ folder thành công!", {
            "Source": result['source_type'],
            "Files tìm thấy": result['total_files'],
            "Folders": folder_stats.get('total_folders', 0),
            "Tổng dòng": result['total_rows'],
            "Kết quả": "1 sheet tổng hợp",
            "File kết quả": output_filename
        })
        
        flash(success_message, 'success')
        
        # Lưu download URL vào session để hiển thị trong template
        session['merge_folder_download_url'] = url_for('main.download_file', filename=output_filename)
        session['merge_folder_result_info'] = {
            'source_type': result['source_type'],
            'total_files': result['total_files'],
            'total_folders': folder_stats.get('total_folders', 0),
            'total_rows': result['total_rows'],
            'total_sheets': result['total_sheets'],
            'output_filename': output_filename,
            'product_analysis': result.get('product_analysis', {})
        }
        
        return redirect(url_for('main.index', _anchor='merge-excel-folder-tab'))
    
    except Exception as e:
        # Cleanup temp files nếu có lỗi
        cleanup_files = []
        if 'temp_zip_path' in locals():
            cleanup_files.append(temp_zip_path)
        if 'temp_folder' in locals():
            cleanup_files.append(temp_folder)
            
        for temp_path in cleanup_files:
            try:
                if os.path.isfile(temp_path):
                    os.unlink(temp_path)
                elif os.path.isdir(temp_path):
                    import shutil
                    shutil.rmtree(temp_path)
            except:
                pass
        
        error_msg = f"Lỗi khi gộp file từ folder: {str(e)}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        
        progress.error(error_msg)
        flash(error_msg, 'error')
        
        return redirect(url_for('main.index'))



@main_bp.route('/merge-excel-folder', methods=['POST'])
@progress_tracker(name="Gộp file Excel từ folder", total_steps=100, verbose=True)
def merge_excel_folder(progress: TerminalProgressBar):
    """
    Gộp file Excel từ folder structure hoặc ZIP file
    """
    try:
        progress.update(5, "Kiểm tra dữ liệu đầu vào")
        
        upload_type = request.form.get('upload_type', 'zip')  # 'zip' hoặc 'folder'
        
        if upload_type == 'zip':
            # Xử lý ZIP file upload
            if 'zip_file' not in request.files:
                progress.error("Không tìm thấy file ZIP!")
                flash('Vui lòng chọn một file ZIP!', 'error')
                return redirect(url_for('main.index'))
            
            zip_file = request.files['zip_file']
            
            if zip_file.filename == '':
                progress.error("Không có file ZIP nào được chọn!")
                flash('Vui lòng chọn một file ZIP!', 'error')
                return redirect(url_for('main.index'))
            
            if not allowed_file(zip_file.filename, ALLOWED_EXTENSIONS_ZIP):
                progress.error("File phải có định dạng .zip!")
                flash('Chỉ chấp nhận file .zip!', 'error')
                return redirect(url_for('main.index'))
            
            # Lưu ZIP file tạm
            temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
            temp_zip_path = temp_zip.name
            temp_zip.close()
            zip_file.save(temp_zip_path)
            
            progress.update(10, f"Đã lưu ZIP file: {zip_file.filename}")
            
            # Chuẩn bị tham số
            source_path = temp_zip_path
            merge_kwargs = {'zip_path': temp_zip_path}
            source_info = f"ZIP: {zip_file.filename}"
            
        elif upload_type == 'folder':
            # Xử lý folder upload (HTML5 webkitdirectory)
            if 'folder_files' not in request.files:
                progress.error("Không tìm thấy file từ folder!")
                flash('Vui lòng chọn một folder chứa file Excel!', 'error')
                return redirect(url_for('main.index'))
            
            files = request.files.getlist('folder_files')
            
            if not files or all(f.filename == '' for f in files):
                progress.error("Không có file nào được chọn từ folder!")
                flash('Vui lòng chọn folder chứa file Excel!', 'error')
                return redirect(url_for('main.index'))
            
            # Tạo thư mục tạm để lưu folder structure
            temp_folder = tempfile.mkdtemp(prefix="excel_folder_")
            
            # Lưu files với cấu trúc folder gốc
            saved_files = 0
            for file in files:
                if file.filename:
                    # Tạo đường dẫn đầy đủ trong temp folder
                    file_path = os.path.join(temp_folder, secure_filename(file.filename))
                    
                    # Tạo thư mục con nếu cần
                    os.makedirs(os.path.dirname(file_path), exist_ok=True)
                    
                    # Lưu file
                    file.save(file_path)
                    saved_files += 1
            
            progress.update(10, f"Đã lưu {saved_files} files từ folder")
            
            # Chuẩn bị tham số
            source_path = temp_folder
            merge_kwargs = {'folder_path': temp_folder}
            source_info = f"Folder: {saved_files} files"
            
        else:
            progress.error("Loại upload không hợp lệ!")
            flash('Loại upload không được hỗ trợ!', 'error')
            return redirect(url_for('main.index'))
        
        # Tạo file output
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"merged_excel_from_{upload_type}_{timestamp}.xlsx"
        output_path = os.path.join(current_app.config['UPLOAD_FOLDER'], output_filename)
        
        # Đảm bảo thư mục upload tồn tại
        os.makedirs(current_app.config['UPLOAD_FOLDER'], exist_ok=True)
        
        progress.update(15, f"Khởi tạo ExcelMerger cho {source_info}")
        
        # Khởi tạo ExcelMerger
        merger = ExcelMerger()
        
        def progress_callback(percent, message):
            """Callback để cập nhật progress cho user"""
            # Chuyển đổi percent từ merger (0-100) sang percent tổng (15-90)
            total_percent = 15 + (percent * 75 // 100)
            progress.update(total_percent, message)
            
            # Gửi qua socketio cho real-time update
            socketio.emit('progress_update', {
                'percent': total_percent,
                'message': message
            })
        
        # Thực hiện gộp file từ folder structure
        result = merger.merge_from_folder_structure(
            output_path=output_path,
            progress_callback=progress_callback,
            **merge_kwargs
        )
        
        # Cleanup temp files
        try:
            if upload_type == 'zip' and 'temp_zip_path' in locals():
                os.unlink(temp_zip_path)
            elif upload_type == 'folder' and 'temp_folder' in locals():
                import shutil
                shutil.rmtree(temp_folder)
        except Exception as e:
            logger.warning(f"Không thể xóa temp files: {str(e)}")
        
        if not result.get('success', False):
            progress.error(f"Lỗi: {result.get('error', 'Không xác định')}")
            flash(f'Lỗi khi gộp file từ {upload_type}: {result.get("error", "Không xác định")}', 'error')
            return redirect(url_for('main.index'))
        
        progress.update(95, "Hoàn tất gộp file từ folder")
        
        # Tạo thông báo thành công với thống kê folder
        folder_stats = result.get('folder_structure', {})
        success_message = f"""
        Gộp file từ {result['source_type']} thành công! 
        - Tổng files: {result['total_files']}
        - Tổng folders: {folder_stats.get('total_folders', 0)}
        - Dòng dữ liệu: {result['total_rows']}
        - Kết quả: 1 sheet chứa toàn bộ dữ liệu
        """
        
        # Gửi thông báo thành công qua socketio
        socketio.emit('progress_update', {
            'percent': 100,
            'message': 'Gộp file Excel từ folder thành công!',
            'success': True,
            'download_url': url_for('main.download_file', filename=output_filename),
            'statistics': {
                'source_type': result['source_type'],
                'total_files': result['total_files'],
                'total_rows': result['total_rows'],
                'total_sheets': result['total_sheets'],
                'folder_structure': folder_stats
            }
        })
        
        progress.complete("success", "Gộp file từ folder thành công!", {
            "Source": result['source_type'],
            "Files tìm thấy": result['total_files'],
            "Folders": folder_stats.get('total_folders', 0),
            "Tổng dòng": result['total_rows'],
            "Kết quả": "1 sheet tổng hợp",
            "File kết quả": output_filename
        })
        
        flash(success_message, 'success')
        
        # Lưu download URL vào session để hiển thị trong template
        session['merge_folder_download_url'] = url_for('main.download_file', filename=output_filename)
        session['merge_folder_result_info'] = {
            'source_type': result['source_type'],
            'total_files': result['total_files'],
            'total_folders': folder_stats.get('total_folders', 0),
            'total_rows': result['total_rows'],
            'total_sheets': result['total_sheets'],
            'output_filename': output_filename,
            'product_analysis': result.get('product_analysis', {})
        }
        
        return redirect(url_for('main.index', _anchor='merge-excel-folder-tab'))
    
    except Exception as e:
        # Cleanup temp files nếu có lỗi
        cleanup_files = []
        if 'temp_zip_path' in locals():
            cleanup_files.append(temp_zip_path)
        if 'temp_folder' in locals():
            cleanup_files.append(temp_folder)
            
        for temp_path in cleanup_files:
            try:
                if os.path.isfile(temp_path):
                    os.unlink(temp_path)
                elif os.path.isdir(temp_path):
                    import shutil
                    shutil.rmtree(temp_path)
            except:
                pass
        
        error_msg = f"Lỗi khi gộp file từ folder: {str(e)}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        
        progress.error(error_msg)
        flash(error_msg, 'error')
        
        return redirect(url_for('main.index'))

