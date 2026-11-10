# Ghi chú Lab Day 04 — Status Investigation Agent

## 1. Case và lỗi được chọn

**Lỗi:** agent có thể trả lời đúng nội dung nhưng bỏ qua URL của incident mà nó đã đọc bằng `get_incident_details`, nên người đọc không kiểm được nguồn.

**Bằng chứng trước khi cải tiến (eval "before", gate tắt):** trong 16 case có ít nhất một incident được đọc thành công, 7 case có câu trả lời thiếu ít nhất một URL đã quan sát:

| Case | URL đã quan sát | URL thiếu trong câu trả lời | Điểm before |
|---|:-:|:-:|:-:|
| E2 | 1 | 1 | 95 |
| E3 | 1 | 1 | 100 |
| E4 | 1 | 1 | 100 |
| E5 | 1 | 1 | 90 |
| A6 | 2 | 2 | 100 |
| H5 | 1 | 1 | 100 |
| H8 | 6 | 6 | 0 |

Case được phân tích sâu là **H8** (điểm 0): agent gọi `get_incident_details` sáu lần, dùng hết 8 bước, không có câu trả lời cuối. Trong trace before, không có event nào giải thích vì sao agent không kết thúc.

## 2. Thay đổi

Evidence gate trong `agent.py` (`run_agent`):

- Lưu URL từ mọi `get_incident_details` có `ok: true`.
- Khi model trả câu trả lời cuối mà không chứa URL nào trong số đó, ghi event `validation_missing_source` vào trace và thêm một message yêu cầu model sửa câu trả lời, trong giới hạn `max_steps`.
- Hết bước mà vẫn thiếu URL thì trả `status: max_steps`, không coi là hoàn thành.
- Biến `EVIDENCE_GATE=0` tắt gate; chỉ dùng cho baseline "before".

Lý do chọn: `tests/test_validation.py` yêu cầu hành vi này, và bằng chứng trên cho thấy lỗi xảy ra thường xuyên.

**Giới hạn của kiểm tra:** gate chỉ kiểm tra sự hiện diện của *một* URL, không chứng minh mọi khẳng định đều có nguồn.

## 3. Bằng chứng trong trace sau cải tiến

Gate kích hoạt ở 7 case (E2, E4, E5, A5, A7, H1, H6). Trong cả 7 case, câu trả lời sau khi sửa đều chứa URL đã quan sát. Ví dụ E2:

- step 1–2: `get_status` rồi `get_incident_details` cho `wp49bp8bqo6c`.
- step 3: model trả câu trả lời cuối thiếu URL → event `validation_missing_source` với `expected_urls` là URL incident đó.
- step 4: model trả lời lại với URL → `completed`, điểm 100.

H8 sau cải tiến: `completed` với 3 model calls và 2 tool calls, không có event gate. Sự khác biệt này đến từ biến thiên của model (xem mục 5), không phải từ gate.

## 4. So sánh before và after

| Chỉ số | Before (gate tắt) | After (gate bật) |
|---|:-:|:-:|
| Điểm tổng | 86.14 | 95.55 |
| Case chấm được | 22/22 | 22/22 |
| Case `max_steps` | 1 (H8) | 0 |
| Vi phạm kiểm tra code (`get_status` không được gọi) | H2, H6 | 0 |
| Tổng token (agent) | 86.415 | 114.135 |
| Tổng token (judge) | 53.140 | 63.238 |

Chênh lệch theo case (điểm before → after):

| Case | Before | After | Δ | Ghi chú |
|---|:-:|:-:|:-:|---|
| E2 | 95 | 100 | +5 | gate kích hoạt, URL được thêm |
| E4 | 100 | 96 | −4 | gate kích hoạt, URL được thêm; điểm giảm, nguyên nhân chưa rõ |
| E5 | 90 | 98 | +8 | gate kích hoạt |
| A1 | 98 | 95 | −3 | không có gate |
| A2 | 99 | 97 | −2 | không có gate |
| A5 | 93 | 92 | −1 | gate kích hoạt |
| A7 | 94 | 100 | +6 | gate kích hoạt |
| A8 | 94 | 100 | +6 | không có gate |
| H2 | 50 | 97 | +47 | agent gọi thêm `get_status` |
| H6 | 50 | 86 | +36 | gate kích hoạt; vi phạm `get_status` đã được sửa |
| H7 | 43 | 50 | +7 | lần chạy after được chạy lại riêng (xem mục 5) |
| H8 | 0 | 96 | +96 | `max_steps` → completed; không có gate |
| Các case còn lại | | | 0 đến +4 | |

Số model calls và tool calls thay đổi ở nhiều case không có gate, ví dụ A1 (8 → 6 model calls), H3 (2 → 3). Điều này cho thấy biến thiên giữa các lần chạy là đáng kể.

## 5. Giới hạn và cách đọc kết quả

- **Một lần chạy mỗi phiên bản.** Chênh lệch dưới khoảng 5 điểm ở một case (ví dụ E4, A1, A2) có thể là nhiễu. Mức tăng lớn ở H2, H6, H8 có liên quan tới hành vi tool nhưng cũng cần lặp lại để khẳng định.
- **Judge cùng model với agent** (`openai/gpt-oss-120b`). Điểm có thể thiên lệch; không so sánh với judge khác.
- **H7 của lần after được chạy lại riêng.** Lần chạy đầy đủ có H7 bị `unscored` do lỗi provider sau khi hết số lần retry. H7 được chạy lại với cùng config (`--case H7`), kết quả thay thế vào `eval_after.json` và tổng được tính lại. Bản gốc của lần chạy đó được lưu tại scratchpad dưới tên `eval_after_run2_with_H7_unscored.json`.
- **Lần after đầu tiên bị treo** ở H1 khoảng 20 phút, nên được dừng và chạy lại từ đầu với cùng config. Kết quả trong `eval_after.json` là của lần chạy lại.
- **Rate limit Groq** (8.000 token/phút) làm các lần chạy chậm và phải retry. Retry không làm thay đổi nội dung câu trả lời, nhưng có thể ảnh hưởng đến thời gian.
- **Cải tiến không giải quyết mọi lỗi:** E4 giảm 4 điểm sau khi gate kích hoạt, và cần đọc trace để xác định nguyên nhân.

## 6. File nộp

- `agent.py`: đã cải tiến như mục 2.
- `eval_before.json`, `eval_after.json`: cần kiểm tra không có thông tin nhạy cảm trước khi nộp. Lưu ý `.gitignore` hiện chỉ loại `eval_results*.json`, không loại hai file này.
- `providers.py`, `status_tools.py`, `evals/run_evals.py`: thay đổi hỗ trợ chạy với Groq và đọc/ghi UTF-8 (xem `updated-01.md`).
- Tests: 28/28 pass; không sửa `tests/`, `fixtures/`, `evals/cases.json`, `evals/scenarios.py`, `evals/grading.py`.
