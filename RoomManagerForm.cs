using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Net;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace OverFieldLocal
{
    class RoomManagerForm : Form
    {
        readonly Backend backend;
        readonly DataGridView rooms=new DataGridView(), players=new DataGridView();
        readonly Label status=new Label();
        readonly System.Windows.Forms.Timer timer=new System.Windows.Forms.Timer();
        readonly List<Button> actions=new List<Button>();
        bool loading,closed;
        public RoomManagerForm(Backend source)
        {
            backend=source;Text=Loc.S("局域网房间与玩家");BackColor=Theme.Bg;ForeColor=Theme.Text;Font=Theme.Font(10);ClientSize=new Size(960,760);MinimumSize=new Size(900,740);StartPosition=FormStartPosition.CenterParent;
            var layout=new TableLayoutPanel { Dock=DockStyle.Fill,Padding=new Padding(24),ColumnCount=1,RowCount=6,BackColor=Theme.Bg };
            layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute,54));layout.RowStyles.Add(new RowStyle(SizeType.Percent,50));layout.RowStyles.Add(new RowStyle(SizeType.Absolute,68));layout.RowStyles.Add(new RowStyle(SizeType.Percent,50));layout.RowStyles.Add(new RowStyle(SizeType.Absolute,68));layout.RowStyles.Add(new RowStyle(SizeType.Absolute,30));Controls.Add(layout);
            var title=new Label { Text=Loc.S("房间与玩家    ·    ")+backend.HostAddress,Font=Theme.Font(17,FontStyle.Bold),ForeColor=Theme.Text,Dock=DockStyle.Fill };layout.Controls.Add(title,0,0);
            SetupGrid(rooms,new[]{Loc.S("房间"),Loc.S("名称"),Loc.S("场景"),Loc.S("在线"),Loc.S("人数上限"),Loc.S("状态")});SetupGrid(players,new[]{"UID",Loc.S("角色名"),Loc.S("地址"),Loc.S("房间"),Loc.S("场景"),Loc.S("状态")});
            layout.Controls.Add(rooms,0,1);layout.Controls.Add(players,0,3);
            var roomActions=new FlowLayoutPanel { Dock=DockStyle.Fill,Margin=new Padding(0,8,0,8),BackColor=Theme.Bg };
            AddButton(roomActions,Loc.S("创建房间"),CreateRoom);AddButton(roomActions,Loc.S("关闭房间"),async()=>await RoomAction("close"));AddButton(roomActions,Loc.S("开放房间"),async()=>await RoomAction("open"));AddButton(roomActions,Loc.S("删除房间"),async()=>await RoomAction("delete"));AddButton(roomActions,Loc.S("设置人数"),SetCapacity);layout.Controls.Add(roomActions,0,2);
            var playerActions=new FlowLayoutPanel { Dock=DockStyle.Fill,Margin=new Padding(0,8,0,8),BackColor=Theme.Bg };
            AddButton(playerActions,Loc.S("移入房间"),async()=>await Execute(new Dictionary<string,object>{{"action","move"},{"room",Selected(rooms)},{"uid",Selected(players)}}));
            AddButton(playerActions,Loc.S("断开玩家"),async()=>await Execute(new Dictionary<string,object>{{"action","kick"},{"uid",Selected(players)}}));AddButton(playerActions,Loc.S("刷新"),async()=>await RefreshRooms());layout.Controls.Add(playerActions,0,4);
            status.Dock=DockStyle.Fill;status.ForeColor=Theme.Muted;status.Text=Loc.S("管理接口仅在本机开放。");layout.Controls.Add(status,0,5);
            timer.Interval=3000;timer.Tick+=async delegate { await RefreshRooms(); };Shown+=async delegate { await RefreshRooms();if(!closed)timer.Start(); };
            FormClosing+=delegate { closed=true;timer.Stop();timer.Dispose(); };
        }
        void SetupGrid(DataGridView grid,string[] names)
        {
            grid.Dock=DockStyle.Fill;grid.ReadOnly=true;grid.AllowUserToAddRows=false;grid.AllowUserToDeleteRows=false;grid.MultiSelect=false;grid.SelectionMode=DataGridViewSelectionMode.FullRowSelect;grid.RowHeadersVisible=false;grid.BackgroundColor=Theme.Panel;grid.BorderStyle=BorderStyle.None;grid.AutoSizeColumnsMode=DataGridViewAutoSizeColumnsMode.Fill;grid.EnableHeadersVisualStyles=false;
            grid.ColumnHeadersDefaultCellStyle.BackColor=Theme.Border;grid.ColumnHeadersDefaultCellStyle.ForeColor=Theme.Text;grid.DefaultCellStyle.BackColor=Theme.Panel;grid.DefaultCellStyle.ForeColor=Theme.Text;grid.DefaultCellStyle.SelectionBackColor=Color.FromArgb(206,236,237);grid.DefaultCellStyle.SelectionForeColor=Theme.Text;grid.GridColor=Theme.Border;grid.RowTemplate.Height=36;grid.ColumnHeadersHeight=36;
            foreach(string name in names)grid.Columns.Add(name,name);
        }
        void AddButton(Control parent,string text,Func<Task> callback)
        {
            var button=new ActionButton(text) { Dock=DockStyle.None,Width=142,Height=46,Margin=new Padding(0,0,12,0),Font=Theme.Font(10,FontStyle.Bold) };
            button.Click+=async delegate { try { await callback(); } catch(Exception e) { if(!closed)status.Text=Friendly.Describe(e,"管理房间"); } };actions.Add(button);parent.Controls.Add(button);
        }
        uint Selected(DataGridView grid)
        {
            if(grid.SelectedRows.Count==0)throw new InvalidOperationException(Loc.S("请先选中房间或玩家。"));return Convert.ToUInt32(grid.SelectedRows[0].Cells[0].Value);
        }
        Dictionary<string,object> Request(Dictionary<string,object> body)
        {
            int port=backend.Mode=="host"?19300:18300;
            var request=(HttpWebRequest)WebRequest.Create("http://127.0.0.1:"+port+"/");request.Proxy=null;request.Timeout=5000;request.ReadWriteTimeout=5000;request.Headers["X-World-Key"]=File.ReadAllText(Path.Combine(backend.ProfilePath,"admin-key.txt"),Encoding.ASCII).Trim();
            if(body!=null) { request.Method="POST";request.ContentType="application/json";byte[] bytes=Encoding.UTF8.GetBytes(new JavaScriptSerializer().Serialize(body));request.ContentLength=bytes.Length;using(var stream=request.GetRequestStream())stream.Write(bytes,0,bytes.Length); }
            try { using(var response=request.GetResponse())using(var reader=new StreamReader(response.GetResponseStream(),Encoding.UTF8))return new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(reader.ReadToEnd()); }
            catch(WebException error)
            { if(error.Response!=null)using(var reader=new StreamReader(error.Response.GetResponseStream(),Encoding.UTF8))throw new InvalidOperationException(reader.ReadToEnd());throw; }
        }
        async Task RefreshRooms() { if(loading||closed)return;await Execute(null); }
        async Task Execute(Dictionary<string,object> body)
        {
            if(loading||closed)return;loading=true;foreach(var button in actions)button.Enabled=false;
            try
            {
                var data=await Task.Run(()=>Request(body));if(closed)return;
                object selectedRoom=rooms.SelectedRows.Count>0?rooms.SelectedRows[0].Cells[0].Value:null;
                rooms.Rows.Clear();foreach(var value in (System.Collections.IEnumerable)data["rooms"]) { var room=(Dictionary<string,object>)value;int row=rooms.Rows.Add(room["id"],room["name"],room["scene"],room["online"],room["capacity"],room["state"]);if(selectedRoom!=null && Convert.ToUInt32(selectedRoom)==Convert.ToUInt32(room["id"]))rooms.Rows[row].Selected=true; }
                object selectedUid=players.SelectedRows.Count>0?players.SelectedRows[0].Cells[0].Value:null;
                players.Rows.Clear();foreach(var value in (System.Collections.IEnumerable)data["players"]) { var player=(Dictionary<string,object>)value;int row=players.Rows.Add(player["uid"],player["name"],player["ip"],player["room"],player["scene"],Convert.ToBoolean(player["ready"])?Loc.S("在线"):Loc.S("登录中"));if(selectedUid!=null && Convert.ToUInt32(selectedUid)==Convert.ToUInt32(player["uid"]))players.Rows[row].Selected=true; }
                status.Text=Loc.S("在线玩家 ")+data["online"]+"    ·    "+DateTime.Now.ToString("HH:mm:ss")+Loc.S(" 更新");
            }
            catch(Exception e) { if(!closed)status.Text=Loc.S("无法完成操作：")+Friendly.Describe(e,"管理房间"); }
            finally { loading=false;if(!closed)foreach(var button in actions)button.Enabled=true; }
        }
        async Task RoomAction(string action) { await Execute(new Dictionary<string,object>{{"action",action},{"room",Selected(rooms)}}); }
        async Task CreateRoom()
        {
            using(var dialog=new RoomSettingsForm(true))if(dialog.ShowDialog(this)==DialogResult.OK)
                await Execute(new Dictionary<string,object>{{"action","create"},{"name",dialog.RoomName},{"capacity",dialog.Capacity},{"scene",dialog.Scene}});
        }
        async Task SetCapacity()
        {
            uint room=Selected(rooms);using(var dialog=new RoomSettingsForm(false))if(dialog.ShowDialog(this)==DialogResult.OK)
                await Execute(new Dictionary<string,object>{{"action","capacity"},{"room",room},{"capacity",dialog.Capacity}});
        }
    }
    class RoomSettingsForm : Form
    {
        readonly TextBox name=new TextBox();readonly NumericUpDown capacity=new NumericUpDown(),scene=new NumericUpDown();
        public string RoomName { get { return name.Text; } }public int Capacity { get { return (int)capacity.Value; } }public int Scene { get { return (int)scene.Value; } }
        public RoomSettingsForm(bool create)
        {
            Text=create?Loc.S("创建房间"):Loc.S("设置人数上限");Font=Theme.Font(10);BackColor=Theme.Panel;ForeColor=Theme.Text;ClientSize=new Size(460,290);FormBorderStyle=FormBorderStyle.FixedDialog;MaximizeBox=false;MinimizeBox=false;StartPosition=FormStartPosition.CenterParent;
            var layout=new TableLayoutPanel { Dock=DockStyle.Fill,Padding=new Padding(20),ColumnCount=2,RowCount=4 };layout.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,160));layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));foreach(int h in new[]{50,50,50,56})layout.RowStyles.Add(new RowStyle(SizeType.Absolute,h));Controls.Add(layout);
            name.Text=Loc.S("新房间");name.MaxLength=32;name.Dock=DockStyle.Fill;capacity.Minimum=1;capacity.Maximum=50;capacity.Value=20;capacity.Dock=DockStyle.Fill;scene.Maximum=999999;scene.Dock=DockStyle.Fill;
            layout.Controls.Add(new Label { Text=Loc.S("房间名称"),AutoSize=true },0,0);layout.Controls.Add(name,1,0);layout.Controls.Add(new Label { Text=Loc.S("人数上限"),AutoSize=true },0,1);layout.Controls.Add(capacity,1,1);layout.Controls.Add(new Label { Text=Loc.S("场景（0 为默认）"),AutoSize=true },0,2);layout.Controls.Add(scene,1,2);
            name.Enabled=scene.Enabled=create;var confirm=new ActionButton(Loc.S("保存"),true) { DialogResult=DialogResult.OK,Dock=DockStyle.Fill,Margin=new Padding(0,8,0,0) };layout.Controls.Add(confirm,1,3);AcceptButton=confirm;
        }
    }
}
