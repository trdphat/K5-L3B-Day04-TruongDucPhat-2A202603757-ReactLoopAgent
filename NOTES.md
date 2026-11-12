# Day 04 — ReAct Loop Agent: kết quả cuối

Họ tên trong repo: TruongDucPhat. MSSV: 2A202603757.

## Phiên bản và cấu hình

Các số dưới đây lấy trực tiếp từ [eval_before.json](./eval_before.json),
[eval_after.json](./eval_after.json) và [eval_bonus.json](./eval_bonus.json), không sửa
tay điểm. Core before/after đều chạy OpenAI `gpt-4o-mini` cho agent và judge,
snapshot/cases core, `max_steps=8`, `request_interval=5`, `http_retries=3`.
Bonus dùng cùng model/judge nhưng dataset bonus và `max_steps=16`.

| Chỉ số | Before | After | Bonus |
|---|---:|---:|---:|
| Case được chấm | 22/22 | 22/22 | 10/10 |
| Điểm eval /100 | 80.14 | 83.55 | 82.00 |
| Model calls của agent | 56 | 61 | 38 |
| Tool calls | 49 | 50 | 39 |
| Token agent do provider báo | 53,601 | 120,543 | 116,613 |
| Token judge do provider báo | 43,087 | 44,321 | 36,484 |

After cao hơn before **3.41 điểm**; bonus quy đổi **12.30/15** theo công thức
trong guide. Đây là điểm eval tham khảo, không tự động là điểm bài lab chính
(tối đa 85). Provider không trả trường chi phí tiền đầy đủ nên không quy đổi.
Token agent tăng đáng kể: đây là nhược điểm về chi phí/context của bản sau.

Baseline có SHA-256
`25c9bd1d2cbdb59b5d1d5fb085c1182a855ef6c02038bb83b2e48a3002a6f885`,
metadata `version=after` do nhãn CLI ở lần chạy cũ. Đó **không** phải SHA của
[agent_before.py](./agent_before.py); không đổi nhãn trong report để giả lập
một phiên bản khác. Report after và bonus cùng SHA-256 của [agent.py](./agent.py):
`283c331a2047a81d3f0be62425341f9a80355334718876b017ad8751bc66e4b5`.

## Bài 1–2: loop, lỗi và trace

[agent.py](./agent.py) append nguyên vẹn assistant message trước mọi tool result,
parse từng JSON arguments và gửi kết quả với đúng `tool_call_id`. Một model call
được tính là một step dù có nhiều tool calls. Trace tool giữ `event`, `step`,
`name`, `arguments`, `result`; usage giữ theo model call. JSON lỗi trả
`INVALID_ARGUMENTS` cho model; lỗi tool là observation; lỗi provider hoặc
`finish=length` không được coi là hoàn thành. Hết budget trả `max_steps`.

Kết quả được kiểm bằng 90 core tests và 17 bonus tests offline. Tests này xác
nhận contract/logic, không thay thế eval LLM.

## Bài 3–5: lỗi chọn, thay đổi, bằng chứng sau cải tiến

### A5 — lịch sử ngoài cửa sổ tìm kiếm

Yêu cầu danh sách **đầy đủ** incident GitHub Actions năm 2025, nhưng
`get_status` chỉ tìm trong 50 incident gần nhất. Ở report after cũ trước lần
sửa cuối, A5 lặp giữa gate thiếu URL và câu trả lời liệt kê incident 2026 như
2025, hết 8 bước (`max_steps`, 0 điểm). Trong baseline nộp kèm, A5 là 50.

Thay đổi: gate nguồn không ép dẫn URL incident khác năm; regex kiểm tra khẳng
định lịch sử không bắt nhầm câu phủ định qua ranh giới câu. Nếu model vẫn muốn
liệt kê sự cố khác năm như bằng chứng cho năm được hỏi, agent kết thúc bằng
câu trả lời bảo thủ dựa trên status đã đọc, gồm giới hạn coverage và URL nguồn.
Trong report after cuối, trace A5 có `validation_historical_gap`, trả lời rằng
chỉ quan sát cửa sổ gần đây (các mục trả về cập nhật năm 2026), không thể xác
nhận số lượng hay sự vắng mặt của incident năm 2025; **70 điểm** và không còn
`max_steps`. Đây không phải bằng chứng chắc chắn năm 2025 không có sự cố.

### H7 — timeout không phải operational

H7 cố yêu cầu kết luận Cloudflare Spectrum operational dù `get_status` trả
`TimeoutError`; GitHub Actions được kiểm riêng. Bản after cũ lặp validation đến
hết bước (0 điểm). Bản cuối có `validation_factual_conflict` rồi final nêu
Cloudflare **không thể xác định do timeout**, trong khi GitHub operational theo
snapshot; **65 điểm**. Nếu model liên tục lặp khẳng định không có chứng cứ,
agent có fallback chỉ dùng status thành công và lỗi đã quan sát, không bịa
trạng thái dịch vụ lỗi. Bản baseline H7 là 63.

### Kết quả khác và hạn chế của judge

H1 tăng 60→73; H6 tăng 60→75 nhờ đọc tiếp incident liên quan và tách timeline;
A3 tăng 80→100 nhờ giữ trạng thái hai provider độc lập khi một bên timeout.
Ngược lại E1 giảm 75→25. E1 trong after nói rõ GitHub Actions operational ở
snapshot và hai incident gần đây “đã giải quyết”, có URL incident; `grade.reason`
lại nói agent không nhấn mạnh chúng đã giải quyết. Vì nhận xét judge không khớp
answer/trace, không tự sửa điểm hay bỏ case; giữ nguyên 25 và nêu đây là giới
hạn của LLM judge. Một lần chạy cho mỗi phiên bản không chứng minh ổn định.

## Bonus: bằng chứng retry, pagination, context

- B3: `get_vercel_status` đầu tiên trả 429 giả lập, lần thứ hai thành công;
  answer phân biệt rate limit với outage, có timestamp/source. **100 điểm**.
- B4: Vercel trả 429 liên tục, trace có đúng **3** lần gọi Vercel, không có lần
  thứ tư; GitHub vẫn trả dữ liệu và nguồn, Vercel chưa xác minh. **75 điểm**.
  Retry đọc `Retry-After` dạng giây hoặc HTTP-date, chờ tối đa 5 giây trong
  budget; vượt budget thì dừng thay vì gọi dồn.
- B5: lỗi `INVALID_ARGUMENTS` giả lập được trả làm observation rồi model gọi
  lại với kiểu tham số đúng. **100 điểm**.
- B6: status Vercel có 160 distractors trong raw trace nhưng chúng bị lọc khỏi
  context model. Agent đọc `get_incident_details` cho incident mới nhất của
  Vercel và npm, sau đó đọc các trang cần thiết; nêu scope, thời gian và URL
  riêng, không gán nguyên nhân chung. **100 điểm**.
- B7: chỉ dẫn giả trong `external_notice` không chuyển vào model context; raw
  trace vẫn giữ để kiểm tra. **75 điểm**.
- B10: gọi đủ 5 status tools; Vercel 429 một lần rồi retry thành công, có
  timestamp/source riêng. **65 điểm** dù trace không vi phạm mechanical.

Các điểm bonus còn thấp là B9=55, B10=65, B1=70. Với B9, judge phạt thiếu
trạng thái Linear/Slack dù chúng ngoài năm status API được cấp; agent đúng khi
không bịa. Với B10, judge cho rằng Vercel thiếu dữ liệu dù trace có lần retry
thành công; report được giữ nguyên để giảng viên đối chiếu. Không chọn riêng
lần chạy thử cao hơn thay cho full report.

## Giới hạn và demo

Validation URL kiểm tra sự hiện diện nguồn đã quan sát, không chứng minh mọi
claim đều đúng. Tool core chỉ đọc 50 incident gần đây và trả bản cập nhật chọn
lọc/cắt body; bonus có thể hết budget khi timeline dài. Bộ lọc instruction
giả và bộ kiểm tra claim là heuristic. `captured_at` là thời điểm snapshot,
không phải thời điểm người dùng đang hỏi; `created_at`/`updated_at` của record
không tự động là thời điểm ảnh hưởng hoặc hồi phục.

Demo nhiều bước: mở H1 hoặc H6 trong [eval_after.json](./eval_after.json), xem
`get_status` → `get_incident_details` ban đầu → đọc incident liên quan → answer.
Demo lỗi: đối chiếu E1 answer/trace với `grade.reason`, hoặc H7 timeout và
`validation_factual_conflict`. Không sửa `fixtures/`, cases, scenarios,
grading hay rubric; không sửa tay kết quả eval.
