# Ghi chú phân tích — Day 04

Tên trong repo: TruongDucPhat. MSSV: 2A202603757.

## 1. Trạng thái và phiên bản được so sánh

Bài 1–2 đã có agent loop tự viết, không dùng framework agent. Bài 4 được làm
lại sau khi quan sát lỗi thực tế và hồi quy trong các lần eval.

Hai report **đã có trên đĩa trước lần sửa hiện tại**:

| Chỉ số | [eval_before.json](./eval_before.json) | [eval_after.json](./eval_after.json) |
|---|---:|---:|
| Case được chấm | 22/22 | 22/22 |
| Điểm eval /100 | 80.14 | 80.68 |
| Model responses của agent | 56 | 68 |
| Tool calls | 49 | 39 |
| Lượt sửa nguồn | 1 | 8 |
| Lượt tự rà soát bắt buộc | 0 | 10 |
| Token agent do provider báo | 53,601 | 208,494 |
| Token judge do provider báo | 43,087 | 35,059 |

Chi phí tiền: provider không cung cấp trường cost đầy đủ nên không quy đổi.
Chênh lệch 0.54 điểm không chứng minh cải tiến ổn định; có hồi quy nghiêm trọng
ở A4 và token agent tăng mạnh.

Cả hai lượt dùng OpenAI, agent/judge gpt-4o-mini, dataset core, max_steps=8,
request_interval=5 giây, http_retries=3. Snapshot và bộ case không bị thay đổi.

Metadata baseline ghi version=after vì nhãn CLI ở lúc chạy, SHA-256:
25c9bd1d2cbdb59b5d1d5fb085c1182a855ef6c02038bb83b2e48a3002a6f885.
Đây vẫn là mốc trước các cải tiến tiếp theo, không phải report của
[agent_before.py](./agent_before.py). Không sửa tay nhãn hoặc điểm trong report.

Report after 80.68 ghi SHA-256:
9881b28101cd2f8c9e2972767f801dcb5b90ad10ec81bd709e8ff52539e30b57.
Nó **chưa đánh giá code hiện tại** sau lần làm lại và thêm bonus.
Chỉ được cập nhật kết luận kết quả mới sau khi runner thực sự ghi report mới.

## 2. Case được chọn và evidence thật

### H1/H6: bỏ sót bước điều tra phụ thuộc

Ở baseline, H1/H6 đều bị giới hạn điểm vì chỉ có tool calls trong một model step.
Agent đọc incident ban đầu nhưng không đọc bản tóm tắt gộp do update dẫn đến.
Đọc một URL trong update không đồng nghĩa đã lấy nội dung đích.

Trong report after 80.68, H1/H6 đã thực sự có bước đọc tiếp và không còn vi phạm
mechanical đó, nhưng điểm chỉ là H1=75, H6=70. Không vì completed hoặc gọi đủ tools
mà kết luận nội dung đã đúng hoàn toàn. H6 còn thiếu mô tả rõ phần body bị cắt;
nguyên nhân, tác động và giới hạn phải được tách riêng.

### A4: hồi quy hết bước khi sửa nguồn

A4 trong report after 80.68 có status=max_steps, answer rỗng và score=0.
Tool get_incident_details đã thành công, trả source:
https://stspg.io/pjgptg3cfc6n.

Trích trace của report, lược bỏ các trường không liên quan:

~~~json
{
  "event": "validation_missing_source",
  "step": 2,
  "observed_urls": ["https://stspg.io/pjgptg3cfc6n"],
  "missing_providers": ["github"]
}
~~~

Cùng loại event lặp ở step 3–8: 7 lần sửa nguồn, không có answer hợp lệ.
Report cũ không lưu bản nháp final nên chưa thể xác định chỉ từ trace đó rằng
model bỏ nguồn do injection hay bộ tách URL nhận sai định dạng Markdown.

Hai vấn đề được tái hiện và xử lý độc lập bằng test:

- URL được bọc bằng Markdown bold không được làm gate hiểu nhầm là thiếu nguồn.
- Chỉ dẫn giả trong update không được chuyển nguyên vẹn thành instruction cho model.
  Trace vẫn giữ raw tool result để đối chiếu, không sửa snapshot hoặc payload chấm.

### H7: tự rà soát không đủ để chống suy đoán

Report after 80.68 có câu trả lời:

> Về Cloudflare Spectrum, truy vấn trạng thái gặp lỗi TimeoutError, do đó trạng thái
> chưa xác minh. Tuy nhiên, trong trường hợp này, có thể coi Cloudflare Spectrum
> đang hoạt động bình thường.

Đây là mâu thuẫn thực sự: không có dữ liệu để khẳng định operational.
Ngay trước đó đã có event validation_evidence_review nhưng model vẫn giữ suy đoán.
Vì vậy không dùng “đã yêu cầu model tự kiểm tra” làm bằng chứng agent an toàn.

## 3. Thay đổi trong bản after làm lại

[agent.py](./agent.py) vẫn đáp ứng contract:

- Append nguyên vẹn assistant message trước tất cả tool results; giữ tool_call_id.
- Parse mọi JSON arguments; JSON sai trả INVALID_ARGUMENTS cho model.
- Lỗi tool là observation; lỗi provider trả error và không lộ exception text.
- Chỉ finish=stop và không còn calls mới xét completed.
- Output bị cắt, lý do dừng bất thường và hết max_steps không bị nhận nhầm là hoàn tất.
- Trace tool vẫn có event, step, name, arguments, result; usage được giữ theo model call.

Cải tiến:

1. Thu gọn prompt, bỏ lượt tự rà soát bắt buộc trên câu trả lời không có lỗi cụ thể.
   Không tăng max_steps của core để che việc lặp.
2. Bộ tách URL nhận Markdown emphasis/backticks và punctuation, tránh vòng sửa nguồn
   chỉ vì định dạng. Gate vẫn cần nguồn khi đã đọc incident thành công và vẫn bị
   giới hạn bởi max_steps.
3. Giữ raw tool result trong trace; chỉ chiếu phần dữ liệu cần thiết vào model context.
   Loại các trường distractors/external_notice/context_note và che nội dung rõ ràng
   đang giả mạo instruction. Không sửa fixtures, scenarios hoặc grader.
4. Tính evidence state trong code: timestamp snapshot, timestamp bản ghi riêng với
   resolved update, số update nhận/tổng/bỏ qua/bị cắt, công việc đọc tiếp và lỗi truy xuất.
   Không chèn một “yêu cầu người dùng mới” mang dữ liệu tool vào trước câu hỏi.
5. Theo liên kết incident chính thức khi câu hỏi cần nguyên nhân/điều tra tiếp.
   Không đọc lặp hoặc chạy vòng trong liên kết. URL alias chỉ được dùng nếu thực sự
   đã quan sát từ tool, không ghép link từ ID chưa có nguồn.
6. Phát hiện một số mâu thuẫn cụ thể: khẳng định trạng thái cho provider không lấy
   được status; phủ định incident lịch sử khi chỉ có cửa sổ dữ liệu gần đây.
   Yêu cầu sửa đúng lỗi và trả lời lại đầy đủ câu hỏi gốc, không bỏ mất các ý trước.

## 4. Bonus: triển khai và kiểm chứng

Không thay đổi [bonus/tools.py](./bonus/tools.py), fixtures hay cases.

- Lấy schemas và system_prompt từ tools khi chúng có tồn tại. Core vẫn fallback về
  schema/prompt bài chính. Bonus sử dụng đủ năm status tools và get_incident_details.
- Provider được nhận từ tên status tool hoặc arguments, không hard-code answer/incident ID.
- RATE_LIMITED và INVALID_ARGUMENTS được trả về model với đúng tool_call_id.
- Tối đa 3 lần RATE_LIMITED liên tiếp cho một tool/provider trước khi chặn thực thi
  tiếp. Trace phân biệt yêu cầu model gửi với lần registry thực sự được thực thi;
  nếu model cố gọi quá giới hạn thì đó vẫn là hành vi cần báo, không xóa trace để lách chấm.
- Retry-After hỗ trợ số giây và HTTP-date. Có thông tin chờ hợp lệ thì chờ trước retry;
  không có header thì backoff 1 giây. Nếu chờ quá 5 giây hoặc header không hiểu được,
  dừng retry thay vì gọi liên tục hoặc ngủ vô hạn. 429 giả lập có delay=0 không ngủ.
- Với câu hỏi timeline/phạm vi/ảnh hưởng, gom các trang theo offset, theo next_offset,
  phát hiện cả lỗ hổng nếu model đã đọc trang cuối nhưng bỏ trang trước.
  Không nhầm tổng số update với số đã đọc, không coi một trang là toàn bộ timeline.
- Lọc 160 bản ghi nhiễu khỏi context model nhưng vẫn giữ trong trace gốc.
  Chỉ dẫn giả trong status/update bị loại khỏi phần model nhận; vẫn có bằng chứng
  nguyên vẹn để debug.
- Mỗi provider có timestamp/source riêng; lỗi một provider không làm mất kết quả
  provider khác. Không đồng nhất rate limit với outage hoặc operational.

Kiểm thử bằng provider giả/scripted responses và snapshot thật của lab:
đây là **test offline**, không phải điểm eval LLM.

~~~powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m unittest discover -s bonus -p "test_*.py" -v
~~~

Các regression test nằm ở [tests/test_agent_evidence.py](./tests/test_agent_evidence.py)
và [bonus/test_agent_extended.py](./bonus/test_agent_extended.py), bên cạnh test gốc.
Test core đã chạy lại sau sửa đổi cuối: **75/75 đạt**.
Test bonus đã chạy đạt **13/13**, gồm 3 test gốc và 10 regression test mới.
Các con số này là test offline, không phải điểm core/bonus từ LLM judge.

## 5. Eval thật cần hoàn tất và giới hạn

Khi thử A4 của bản làm lại từ tiến trình agent, API trả
401 invalid_api_key. File tạm eval_results_after_probe.json ghi 0/1 case được chấm.
Đây là lỗi credential của tiến trình đó, không phải điểm 0 của A4.
Không ghi đè report after 80.68 bằng một report lỗi để giả vờ đã chạy xong bản mới.
Chưa có eval_bonus.json hợp lệ và không tạo bằng fake provider.

Nếu terminal người dùng đang có key hợp lệ, có thể chạy hai lệnh dưới trực tiếp.
Hoặc lưu cấu hình từ chính terminal đó, không gửi key lên chat:

~~~powershell
.\.venv\Scripts\python.exe -X utf8 save_lab_config.py --provider openai --model gpt-4o-mini
~~~

Chạy lại core, giữ nguyên cấu hình so với baseline:

~~~powershell
.\.venv\Scripts\python.exe -X utf8 evals/run_evals.py --provider openai --model gpt-4o-mini --judge-provider openai --judge-model gpt-4o-mini --agent-version after --max-steps 8 --request-interval 5 --http-retries 3 --output eval_after.json
~~~

Chạy đủ 10 case bonus:

~~~powershell
.\.venv\Scripts\python.exe -X utf8 evals/run_evals.py --bonus --provider openai --model gpt-4o-mini --judge-provider openai --judge-model gpt-4o-mini --agent-version after --max-steps 16 --request-interval 5 --http-retries 3 --output eval_bonus.json
~~~

Hai report mới phải có đủ case, không unscored, score khác null và SHA code phù hợp.
Sau đó cập nhật bảng so sánh, case thay đổi, model calls, token/chi phí nếu có.
Bonus chỉ quy đổi khi đủ 10 case: bonus_points_0_15 = round(score_0_100 * 0.15, 2).

Giới hạn:

- Test offline không chứng minh điểm LLM tăng. Chưa có kết quả mới thì không hứa 9x.
- LLM judge có thể mâu thuẫn với question/expected hoặc phạt điều không được yêu cầu;
  phải đối chiếu answer với tool evidence, không làm theo mọi lời khuyên của judge.
- Bộ lọc directive và bộ kiểm tra mâu thuẫn là heuristic, không phải chứng minh mọi
  claim đúng. Lọc quá mạnh có thể làm mất nội dung hợp lệ; không xem đây là bảo vệ
  prompt injection toàn diện.
- Source gate kiểm tra hiện diện URL, không kiểm chứng mọi phát biểu hoặc quan hệ nhân quả.
- Chỉ đọc những gì API/snapshot cung cấp; core không thể phục hồi update bị cắt.
  Bonus có thể hết budget trước khi hết trang; phải báo giới hạn thay vì giả vờ đã đọc hết.
- Một lượt mỗi phiên bản không chứng minh ổn định; có thể cần lặp có kế hoạch,
  không chọn riêng lượt điểm cao rồi bỏ qua các lượt thấp.
- Điểm eval /100 khác điểm bài lab. Theo rubric: main tối đa 85 điểm theo các hạng mục
  triển khai/phân tích/trình bày; bonus tối đa 15 điểm. Không tự lấy eval nhân 0.85
  làm điểm main, cũng không coi eval 80 là điểm lab cuối cùng.
