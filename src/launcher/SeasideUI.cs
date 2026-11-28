using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace OverFieldLocal
{
    // Original vector seaside illustration: no screenshots, accounts or game assets embedded.
    class SeasideHero : Panel
    {
        public SeasideHero() { DoubleBuffered=true; BackColor=Color.FromArgb(214,239,247); }
        protected override void OnPaint(PaintEventArgs e)
        {
            var g=e.Graphics;g.SmoothingMode=SmoothingMode.AntiAlias;
            using(var b=new LinearGradientBrush(ClientRectangle,Color.FromArgb(203,233,249),Color.FromArgb(240,251,247),90f))g.FillRectangle(b,ClientRectangle);
            float x=Width*.69f,y=Height*.42f;
            using(var cloud=new SolidBrush(Color.FromArgb(205,255,255,255)))
            {
                g.FillEllipse(cloud,x+10,18,83,27);g.FillEllipse(cloud,x+38,6,59,43);g.FillEllipse(cloud,x+73,22,78,24);
                g.FillEllipse(cloud,x+190,57,86,23);g.FillEllipse(cloud,x+210,43,48,37);
            }
            using(var sea=new SolidBrush(Color.FromArgb(135,208,218)))g.FillRectangle(sea,x-18,Height-42,Width-x+20,42);
            using(var wave=new Pen(Color.FromArgb(215,255,255,255),2))
            {g.DrawBezier(wave,x-2,Height-25,x+70,Height-37,x+138,Height-16,x+235,Height-28);g.DrawLine(wave,x+55,Height-13,x+160,Height-13);}
            using(var ring=new Pen(Color.FromArgb(83,145,195,206),1.5f))g.DrawEllipse(ring,x+10,y-10,150,72);
            PointF[] diamond={new PointF(x+85,y-28),new PointF(x+109,y+3),new PointF(x+85,y+34),new PointF(x+61,y+3)};
            using(var b=new LinearGradientBrush(new RectangleF(x+60,y-28,50,62),Color.FromArgb(71,156,206),Color.FromArgb(149,220,235),45f))g.FillPolygon(b,diamond);
            using(var pen=new Pen(Color.FromArgb(236,255,255,255),2))g.DrawPolygon(pen,diamond);
            using(var leaf=new SolidBrush(Color.FromArgb(124,186,150)))
            {g.FillEllipse(leaf,Width-67,Height-50,24,13);g.FillEllipse(leaf,Width-45,Height-67,19,30);g.FillEllipse(leaf,Width-33,Height-42,25,12);}
            base.OnPaint(e);
        }
    }

    partial class LauncherForm
    {
        Label FitLabel(string text,float size,Color color,FontStyle style=FontStyle.Regular)
        {return new Label{Text=text,Font=Theme.Font(size,style),ForeColor=color,BackColor=Color.Transparent,Dock=DockStyle.Fill,AutoSize=false,AutoEllipsis=true,TextAlign=ContentAlignment.MiddleLeft,Margin=Padding.Empty};}
        TableLayoutPanel Grid(int columns,int rows)
        {return new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=columns,RowCount=rows,Margin=Padding.Empty,Padding=Padding.Empty,BackColor=Color.Transparent};}
        void SpaceButton(ActionButton button,bool last=false)
        {button.Dock=DockStyle.Fill;button.Margin=new Padding(0,4,last?0:16,4);button.Font=Theme.Font(10,FontStyle.Bold);button.MinimumSize=new Size(0,44);}

        void SaveUIPreview(string directory)
        {
            Directory.CreateDirectory(directory);
            badge.Text=Loc.S("界面预览");server.UpdateState(false,Loc.S("尚未启动 · 独立服务"));gateway.UpdateState(false,Loc.S("等待游戏连接"));proxy.UpdateState(false,Loc.S("本地 UID 与房间管理"));
            start.Text=backend.Mode=="host"?Loc.S("发布局域网服务  →"):backend.Mode=="steam"?Loc.S("启动游戏  →"):Loc.S("启动单机游戏  →");
            profileLabel.Text=backend.Mode=="host"?Loc.S("联机存档  ·  本地保存 / 独立备份"):Loc.S("单机存档  ·  本地保存 / 独立备份");
            logs.Text=Loc.S("欢迎回到开放空间。\n选择运行模式，设置自己的 UID，然后开始你的世界。\n此预览未连接官服、未启动游戏或服务。");
            PerformLayout();var errors=new List<string>();AuditUI(this,errors);
            string stem=backend.Mode+"-"+ClientSize.Width+"x"+ClientSize.Height;
            using(var bitmap=new Bitmap(ClientSize.Width,ClientSize.Height)){DrawToBitmap(bitmap,new Rectangle(0,0,ClientSize.Width,ClientSize.Height));bitmap.Save(Path.Combine(directory,stem+".png"));}
            File.WriteAllText(Path.Combine(directory,stem+".json"),new JavaScriptSerializer().Serialize(new Dictionary<string,object>{{"mode",backend.Mode},{"locale",Loc.CurrentLocale},{"page",capturePage != null && capturePage.Visible ? "capture" : "world"},{"size",ClientSize.ToString()},{"layoutErrors",errors},{"checked","Button text fit, interactive sibling overlap, child bounds; no game/service operations"}}));
        }
        void AuditUI(Control parent,List<string> errors)
        {
            var textControls=new List<Control>();
            foreach(Control child in parent.Controls)
            {
                // Hidden pages have not been laid out; inspect them separately
                // while visible, rather than reporting phantom clipping errors.
                if(!child.Visible) continue;
                if(child is ActionButton)
                {
                    Size text=TextRenderer.MeasureText(child.Text,child.Font,new Size(10000,1000),TextFormatFlags.SingleLine|TextFormatFlags.NoPadding);
                    if(text.Width>child.Width-24 || text.Height>child.Height-16)errors.Add("Button text clipped: "+child.Text);
                    if(child.Right>parent.ClientSize.Width+1 || child.Bottom>parent.ClientSize.Height+1)errors.Add("Button outside row: "+child.Text);
                }
                if(child is Label || child is Button || child is TextBox || child is ComboBox || child is NumericUpDown)textControls.Add(child);
                AuditUI(child,errors);
            }
            for(int a=0;a<textControls.Count;a++)for(int b=a+1;b<textControls.Count;b++)
                if(textControls[a].Bounds.IntersectsWith(textControls[b].Bounds))errors.Add("Sibling overlap: "+textControls[a].Text+" / "+textControls[b].Text);
        }

        void BuildLayout()
        {
            var shell=Grid(2,1);shell.BackColor=Theme.Bg;
            shell.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,276));shell.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));Controls.Add(shell);
            var sideScroll=new Panel{Dock=DockStyle.Fill,AutoScroll=true,BackColor=Color.FromArgb(222,240,241),Margin=Padding.Empty};shell.Controls.Add(sideScroll,0,0);
            var side=Grid(1,22);side.Dock=DockStyle.Top;side.Height=628;side.Padding=new Padding(18,14,18,12);
            side.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
            foreach(int height in new[]{36,20,32,24,52,18,3,52,18,3,52,18,3,52,18,3,52,18,8,18,74,4})side.RowStyles.Add(new RowStyle(SizeType.Absolute,height));
            sideScroll.Controls.Add(side);
            sideTitleLabel=FitLabel(Loc.S("开放空间"),16.5f,Theme.Text,FontStyle.Bold); side.Controls.Add(sideTitleLabel,0,0);
            side.Controls.Add(FitLabel("O V E R F I E L D",8.5f,Theme.Muted),0,1);
            var langRow=Grid(2,1);langRow.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,88));langRow.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
            sideLangLabel=FitLabel(Loc.S("语言"),9,Theme.Muted); langRow.Controls.Add(sideLangLabel,0,0);
            languageBox.DropDownStyle=ComboBoxStyle.DropDownList;languageBox.Dock=DockStyle.Fill;languageBox.Font=Theme.Font(9);languageBox.BackColor=Theme.Panel;languageBox.ForeColor=Theme.Text;languageBox.Margin=new Padding(0,4,0,2);
            updatingUiControls=true;
            try { languageBox.Items.Clear(); foreach(string langName in Loc.LocaleDisplayNames) languageBox.Items.Add(langName); languageBox.SelectedIndex=Loc.LocaleIndex(Loc.CurrentLocale); }
            finally { updatingUiControls=false; }
            langRow.Controls.Add(languageBox,1,0); side.Controls.Add(langRow,0,2);
            sideSectionLabel=FitLabel(Loc.S("选择你的世界"),9.5f,Theme.Muted); side.Controls.Add(sideSectionLabel,0,3);

            // Unified world navigation: every language & edition exposes all modes
            // and can run/capture Bilibili, TapTap, and Steam clients.
            SpaceButton(soloMode,true); side.Controls.Add(soloMode,0,4);
            soloDescLabel=FitLabel(Loc.S("存档 A · 全区服可用"),8.5f,Theme.Muted); side.Controls.Add(soloDescLabel,0,5);
            SpaceButton(steamMode,true); side.Controls.Add(steamMode,0,7);
            steamDescLabel=FitLabel(Loc.S("存档 B · 全区服可用"),8.5f,Theme.Muted); side.Controls.Add(steamDescLabel,0,8);
            SpaceButton(hostMode,true); side.Controls.Add(hostMode,0,10);
            hostDescLabel=FitLabel(Loc.S("局域网联机 · 全区服可用"),8.5f,Theme.Muted); side.Controls.Add(hostDescLabel,0,11);
            SpaceButton(captureMode,true); side.Controls.Add(captureMode,0,13);
            captureDescLabel=FitLabel(Loc.S("官方采集（B服 / Tap / Steam）"),8.5f,Theme.Muted); side.Controls.Add(captureDescLabel,0,14);
            SpaceButton(roomsButton,true); side.Controls.Add(roomsButton,0,16);
            roomsDescLabel=FitLabel(Loc.S("查看在线玩家与房间"),8.5f,Theme.Muted); side.Controls.Add(roomsDescLabel,0,17);
            side.Controls.Add(FitLabel("LOCAL WORLD",8,Theme.Accent,FontStyle.Bold),0,19);
            sideFooterDescLabel=FitLabel(Loc.S("以自己的 UID 开始\n数据只保存在本电脑\n请勿公开私人存档"),8.5f,Theme.Muted); side.Controls.Add(sideFooterDescLabel,0,20);

            var scroll=new Panel{Dock=DockStyle.Fill,AutoScroll=true,Margin=Padding.Empty,BackColor=Theme.Bg};shell.Controls.Add(scroll,1,0);mainScroll=scroll;
            var layout=Grid(1,7);layout.Dock=DockStyle.Top;layout.Padding=new Padding(24,18,24,16);layout.Height=860;
            layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
            foreach(int height in new[]{128,86,78,196,118,210,16})layout.RowStyles.Add(new RowStyle(SizeType.Absolute,height));
            scroll.Controls.Add(layout);scroll.Resize+=delegate{
                layout.Height=Math.Max(836,scroll.ClientSize.Height);
                if(capturePage!=null) capturePage.Height=Math.Max(748,scroll.ClientSize.Height);
            };

            var hero=new SeasideHero{Dock=DockStyle.Fill,Margin=new Padding(0,0,0,14),Padding=new Padding(22,10,22,10)};
            var heroGrid=Grid(2,3);heroGrid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,62));heroGrid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,38));
            foreach(int h in new[]{22,46,24})heroGrid.RowStyles.Add(new RowStyle(SizeType.Absolute,h));hero.Controls.Add(heroGrid);
            heroBrandLabel=FitLabel(Loc.S("O V E R F I E L D  /  本 地 世 界"),8.5f,Theme.Muted,FontStyle.Bold);heroGrid.Controls.Add(heroBrandLabel,0,0);
            headline.Text=Loc.S("开放空间");headline.Font=Theme.Font(23,FontStyle.Bold);headline.ForeColor=Theme.Text;headline.Dock=DockStyle.Fill;headline.AutoSize=false;headline.AutoEllipsis=true;headline.BackColor=Color.Transparent;headline.TextAlign=ContentAlignment.MiddleLeft;headline.Margin=Padding.Empty;heroGrid.Controls.Add(headline,0,1);
            subtitle.Font=Theme.Font(9.5f);subtitle.ForeColor=Theme.Muted;subtitle.Dock=DockStyle.Fill;subtitle.AutoSize=false;subtitle.AutoEllipsis=true;subtitle.BackColor=Color.Transparent;subtitle.TextAlign=ContentAlignment.MiddleLeft;subtitle.Margin=Padding.Empty;heroGrid.Controls.Add(subtitle,0,2);
            badge.Text=Loc.S("正在检查");badge.Font=Theme.Font(9,FontStyle.Bold);badge.ForeColor=Theme.Muted;badge.Dock=DockStyle.Fill;badge.AutoEllipsis=true;badge.BackColor=Color.Transparent;badge.TextAlign=ContentAlignment.TopRight;badge.Margin=Padding.Empty;heroGrid.Controls.Add(badge,1,0);
            layout.Controls.Add(hero,0,0);

            var states=Grid(3,1);for(int i=0;i<3;i++)states.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100f/3));states.Margin=new Padding(0,0,0,14);
            server.Margin=gateway.Margin=new Padding(0,0,16,0);proxy.Margin=Padding.Empty;states.Controls.Add(server,0,0);states.Controls.Add(gateway,1,0);states.Controls.Add(proxy,2,0);layout.Controls.Add(states,0,1);
            var actions=Grid(3,1);foreach(float w in new[]{48f,26f,26f})actions.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,w));actions.Margin=new Padding(0,0,0,14);
            SpaceButton(start);start.Font=Theme.Font(11.5f,FontStyle.Bold);SpaceButton(services);SpaceButton(stop,true);actions.Controls.Add(start,0,0);actions.Controls.Add(services,1,0);actions.Controls.Add(stop,2,0);layout.Controls.Add(actions,0,2);

            var settings=new Surface{Dock=DockStyle.Fill,Padding=new Padding(18,10,18,10),Margin=new Padding(0,0,0,14)};
            var settingRows=Grid(1,4);settingRows.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));foreach(int h in new[]{24,48,50,40})settingRows.RowStyles.Add(new RowStyle(SizeType.Absolute,h));settings.Controls.Add(settingRows);
            var settingsHead=Grid(2,1);settingsHead.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,42));settingsHead.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,58));
            settingsTitleLabel=FitLabel(Loc.S("启动设置"),10,Theme.Text,FontStyle.Bold);settingsHead.Controls.Add(settingsTitleLabel,0,0);
            channelStatusLabel=FitLabel("",8.5f,Theme.Accent,FontStyle.Bold);channelStatusLabel.TextAlign=ContentAlignment.MiddleRight;settingsHead.Controls.Add(channelStatusLabel,1,0);
            settingRows.Controls.Add(settingsHead,0,0);

            var channelRow=Grid(3,1);channelRow.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,124));channelRow.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));channelRow.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,146));
            worldChannelLabel=FitLabel(Loc.S("游戏区服"),9.5f,Theme.Muted);channelRow.Controls.Add(worldChannelLabel,0,0);
            worldChannelBox.DropDownStyle=ComboBoxStyle.DropDownList;worldChannelBox.Dock=DockStyle.Fill;worldChannelBox.Font=Theme.Font(9.5f);worldChannelBox.BackColor=Theme.Panel;worldChannelBox.ForeColor=Theme.Text;worldChannelBox.Margin=new Padding(0,10,16,8);
            channelRow.Controls.Add(worldChannelBox,1,0);
            SpaceButton(detectWorldClient,true);detectWorldClient.Font=Theme.Font(9.5f,FontStyle.Bold);detectWorldClient.Margin=new Padding(0,2,0,2);channelRow.Controls.Add(detectWorldClient,2,0);
            settingRows.Controls.Add(channelRow,0,1);

            var pathRow=Grid(2,1);pathRow.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));pathRow.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,146));
            rootPath.Text=String.IsNullOrWhiteSpace(backend.Root)?Loc.S("单机模式：请选择本地客户端目录"):backend.Root;rootPath.ReadOnly=true;rootPath.BorderStyle=BorderStyle.None;rootPath.BackColor=Theme.Panel;rootPath.ForeColor=Theme.Text;rootPath.Font=Theme.Font(9.5f);rootPath.Dock=DockStyle.Fill;rootPath.Margin=new Padding(0,14,16,8);
            SpaceButton(browse,true);browse.Margin=new Padding(0,2,0,2);pathRow.Controls.Add(rootPath,0,0);pathRow.Controls.Add(browse,1,0);settingRows.Controls.Add(pathRow,0,2);

            var uidRow=Grid(4,1);foreach(float w in new[]{50f,152f,138f})uidRow.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,w));uidRow.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
            uidRow.Controls.Add(FitLabel("UID",9.5f,Theme.Muted),0,0);
            uidBox.Minimum=1;uidBox.Maximum=UInt32.MaxValue;uidBox.Value=backend.Uid;uidBox.Dock=DockStyle.Fill;uidBox.Font=Theme.Font(10.5f);uidBox.BackColor=Theme.Panel;uidBox.ForeColor=Theme.Text;uidBox.Margin=new Padding(0,7,20,5);uidRow.Controls.Add(uidBox,1,0);
            addressLabel=FitLabel(Loc.S("服务地址"),9.5f,Theme.Muted);uidRow.Controls.Add(addressLabel,2,0);
            hostBox.DropDownStyle=ComboBoxStyle.DropDownList;hostBox.Dock=DockStyle.Fill;hostBox.Font=Theme.Font(9.5f);hostBox.BackColor=Theme.Panel;hostBox.ForeColor=Theme.Text;hostBox.Margin=new Padding(0,7,0,5);uidRow.Controls.Add(hostBox,3,0);settingRows.Controls.Add(uidRow,0,3);
            layout.Controls.Add(settings,0,3);

            var dataPanel=new Surface{Dock=DockStyle.Fill,Padding=new Padding(18,10,18,12),Margin=new Padding(0,0,0,14)};
            var dataRows=Grid(1,2);dataRows.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));dataRows.RowStyles.Add(new RowStyle(SizeType.Absolute,24));dataRows.RowStyles.Add(new RowStyle(SizeType.Percent,100));dataPanel.Controls.Add(dataRows);
            profileLabel.Dock=DockStyle.Fill;profileLabel.AutoEllipsis=true;profileLabel.Font=Theme.Font(9);profileLabel.ForeColor=Theme.Muted;profileLabel.TextAlign=ContentAlignment.MiddleLeft;profileLabel.Margin=Padding.Empty;dataRows.Controls.Add(profileLabel,0,0);
            var dataActions=Grid(4,1);for(int i=0;i<4;i++)dataActions.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,25));int index=0;foreach(var b in new[]{importData,backupData,openData,firewall}){SpaceButton(b,index==3);b.Margin=new Padding(0,2,index==3?0:16,2);dataActions.Controls.Add(b,index++,0);}dataRows.Controls.Add(dataActions,0,1);layout.Controls.Add(dataPanel,0,4);

            capturePage=new Surface{Dock=DockStyle.Top,Height=748,Padding=new Padding(22,16,22,16),Margin=Padding.Empty,Visible=false};
            var captureRows=Grid(1,10);captureRows.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
            foreach(int h in new[]{30,48,24,52,44,60,26,246,36,84})captureRows.RowStyles.Add(new RowStyle(SizeType.Absolute,h));capturePage.Controls.Add(captureRows);
            captureTitleLabel=FitLabel(Loc.S("公服账号采集"),11,Theme.Text,FontStyle.Bold);captureRows.Controls.Add(captureTitleLabel,0,0);

            var officialChannelRow=Grid(3,1);officialChannelRow.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,124));officialChannelRow.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));officialChannelRow.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,146));
            captureChannelLabel=FitLabel(Loc.S("采集区服"),9.5f,Theme.Muted);officialChannelRow.Controls.Add(captureChannelLabel,0,0);
            captureChannelBox.DropDownStyle=ComboBoxStyle.DropDownList;captureChannelBox.Dock=DockStyle.Fill;captureChannelBox.Font=Theme.Font(9.5f);captureChannelBox.BackColor=Theme.Panel;captureChannelBox.ForeColor=Theme.Text;captureChannelBox.Margin=new Padding(0,9,16,7);
            officialChannelRow.Controls.Add(captureChannelBox,1,0);
            SpaceButton(captureDetectBtn,true);captureDetectBtn.Font=Theme.Font(9.5f,FontStyle.Bold);captureDetectBtn.Margin=new Padding(0,1,0,1);officialChannelRow.Controls.Add(captureDetectBtn,2,0);
            captureRows.Controls.Add(officialChannelRow,0,1);

            capturePathDescLabel=FitLabel(Loc.S("公服启动器路径（仅公服，与私服目录独立）"),9,Theme.Muted);captureRows.Controls.Add(capturePathDescLabel,0,2);
            var officialRow=Grid(2,1);officialRow.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));officialRow.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,146));
            officialLauncherPath.Text=String.IsNullOrWhiteSpace(backend.OfficialLauncherPath)?Loc.S("请选择公服启动器或未修改的原版客户端 EXE"):backend.OfficialLauncherPath;
            officialLauncherPath.ReadOnly=true;officialLauncherPath.BorderStyle=BorderStyle.FixedSingle;officialLauncherPath.BackColor=Theme.Panel;officialLauncherPath.ForeColor=Theme.Text;officialLauncherPath.Font=Theme.Font(9.5f);officialLauncherPath.Dock=DockStyle.Fill;officialLauncherPath.Margin=new Padding(0,10,16,8);
            SpaceButton(officialBrowse,true);officialRow.Controls.Add(officialLauncherPath,0,0);officialRow.Controls.Add(officialBrowse,1,0);captureRows.Controls.Add(officialRow,0,3);
            captureHint.Dock=DockStyle.Fill;captureHint.AutoSize=false;captureHint.AutoEllipsis=true;captureHint.Font=Theme.Font(9);captureHint.ForeColor=Theme.Muted;captureHint.TextAlign=ContentAlignment.TopLeft;captureHint.Margin=Padding.Empty;
            captureHint.Text=Loc.S("抓包就绪后会打开公服。结束采集将生成账号 ZIP，并让你选择保存位置；入库是可选操作，取消或冲突不影响已交付的 ZIP。");
            captureRows.Controls.Add(captureHint,0,4);
            var captureActions=Grid(4,1);for(int i=0;i<4;i++)captureActions.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,25));
            SpaceButton(capturePreflight);SpaceButton(captureStart);SpaceButton(captureStop);SpaceButton(captureSaveZip,true);
            captureActions.Controls.Add(capturePreflight,0,0);captureActions.Controls.Add(captureStart,1,0);captureActions.Controls.Add(captureStop,2,0);captureActions.Controls.Add(captureSaveZip,3,0);
            captureRows.Controls.Add(captureActions,0,5);
            captureListTitleLabel=FitLabel(Loc.S("采集项目清单 · 1.0.12增强集成 · 实时/最终核对"),9.5f,Theme.Text,FontStyle.Bold);captureRows.Controls.Add(captureListTitleLabel,0,6);
            captureChecklist.Dock=DockStyle.Fill;captureChecklist.View=View.Details;captureChecklist.FullRowSelect=true;captureChecklist.MultiSelect=false;captureChecklist.HideSelection=false;
            captureChecklist.Font=Theme.Font(9);captureChecklist.BackColor=Theme.Panel;captureChecklist.ForeColor=Theme.Text;captureChecklist.BorderStyle=BorderStyle.FixedSingle;captureChecklist.Margin=new Padding(0,4,0,4);
            captureChecklist.Columns.Add(Loc.S("项目"),210);captureChecklist.Columns.Add(Loc.S("状态"),150);captureChecklist.Columns.Add(Loc.S("响应"),60);captureChecklist.Columns.Add(Loc.S("未采集时的操作建议"),250);
            PopulateInitialChecklistRows();
            captureChecklist.Resize+=delegate { if(captureChecklist.Columns.Count==4) captureChecklist.Columns[3].Width=Math.Max(190,captureChecklist.ClientSize.Width-440); };
            captureRows.Controls.Add(captureChecklist,0,7);
            captureProgressHint.Dock=DockStyle.Fill;captureProgressHint.AutoSize=false;captureProgressHint.AutoEllipsis=true;captureProgressHint.Font=Theme.Font(8.5f);captureProgressHint.ForeColor=Theme.Muted;captureProgressHint.Margin=new Padding(0,4,0,0);
            captureProgressHint.Text=Loc.S("只有成功解码的公服响应才算已采集；快照暂不可读会显示暂时无法判断，结束后按完整数据核对。");
            captureRows.Controls.Add(captureProgressHint,0,8);
            captureSteps.Dock=DockStyle.Fill;captureSteps.AutoSize=false;captureSteps.Font=Theme.Font(8.5f);captureSteps.ForeColor=Theme.Muted;captureSteps.TextAlign=ContentAlignment.TopLeft;captureSteps.Margin=Padding.Empty;
            captureSteps.Text=Loc.S("1. 管理员运行，选择公服原版 EXE；不要选择私服副本\n2. 开始采集并登录，按清单打开缺少的界面并等待加载\n3. 结束后核对完整清单并另存 ZIP；入库可取消，私人数据勿公开");
            captureRows.Controls.Add(captureSteps,0,9);
            scroll.Controls.Add(capturePage);

            var logPanel=new Surface{Dock=DockStyle.Fill,Margin=Padding.Empty,Padding=new Padding(18,10,18,12)};
            var logRows=Grid(1,2);logRows.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));logRows.RowStyles.Add(new RowStyle(SizeType.Absolute,28));logRows.RowStyles.Add(new RowStyle(SizeType.Percent,100));logPanel.Controls.Add(logRows);
            var logHead=Grid(2,1);logHead.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));logHead.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,176));
            logsTitleLabel=FitLabel(Loc.S("运行记录"),10,Theme.Text,FontStyle.Bold);logHead.Controls.Add(logsTitleLabel,0,0);
            openLogsLink=new LinkLabel{Text=Loc.S("打开日志文件夹"),LinkColor=Theme.Muted,ActiveLinkColor=Theme.Accent,VisitedLinkColor=Theme.Muted,Dock=DockStyle.Fill,TextAlign=ContentAlignment.MiddleRight,AutoSize=false};openLogsLink.LinkClicked+=delegate{OpenFolder(Path.Combine(backend.ProfilePath,"logs"));};logHead.Controls.Add(openLogsLink,1,0);logRows.Controls.Add(logHead,0,0);
            logs.Dock=DockStyle.Fill;logs.ReadOnly=true;logs.BackColor=Theme.Panel;logs.ForeColor=Theme.Muted;logs.BorderStyle=BorderStyle.None;logs.Font=Theme.Font(9);logs.DetectUrls=false;logs.ScrollBars=RichTextBoxScrollBars.Vertical;logs.Margin=new Padding(0,4,0,0);logRows.Controls.Add(logs,0,1);layout.Controls.Add(logPanel,0,5);

            PopulateChannelComboBoxes();
            UpdateChannelBadge();

            // A compact persistent strip, not a fifth navigation item. It stays visible on capture pages too.
            var bottom=Grid(1,2);bottom.Dock=DockStyle.Bottom;bottom.Height=82;bottom.Padding=new Padding(24,4,24,4);
            bottom.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));bottom.RowStyles.Add(new RowStyle(SizeType.Absolute,48));bottom.RowStyles.Add(new RowStyle(SizeType.Absolute,26));
            bottom.Controls.Add(BuildUpdaterStrip(),0,0);
            footer.Text=Loc.S("游戏与服务独立运行  ·  单机 / 联机存档分开保存");footer.ForeColor=Theme.Muted;footer.Font=Theme.Font(8.5f);footer.Dock=DockStyle.Fill;footer.AutoEllipsis=true;footer.TextAlign=ContentAlignment.BottomLeft;footer.Margin=Padding.Empty;bottom.Controls.Add(footer,0,1);
            Controls.Add(bottom);shell.BringToFront();
        }
    }
}
