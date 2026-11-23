#import <Cocoa/Cocoa.h>
#include <signal.h>

@interface LeadAppDelegate : NSObject <NSApplicationDelegate>
@property NSTask *service;
@property NSStatusItem *statusItem;
@property NSURL *workbenchURL;
@property NSURL *logURL;
@property NSTimer *timer;
@property BOOL ready;
@property BOOL noOpen;
@property BOOL checking;
@property BOOL stopping;
@property NSInteger attempts;
@property NSArray<NSString *> *serverArguments;
@property dispatch_source_t terminationSignal;
@end

@implementation LeadAppDelegate
- (void)showError:(NSString *)message {
    [self.timer invalidate];
    NSAlert *alert = [[NSAlert alloc] init];
    alert.messageText = @"客户线索工具未能启动";
    alert.informativeText = message;
    [alert addButtonWithTitle:@"关闭"]; [alert addButtonWithTitle:@"查看日志"];
    [NSApp activateIgnoringOtherApps:YES];
    if ([alert runModal] == NSAlertSecondButtonReturn) [self openLog:nil];
    [NSApp terminate:nil];
}
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    signal(SIGTERM, SIG_IGN);
    self.terminationSignal = dispatch_source_create(DISPATCH_SOURCE_TYPE_SIGNAL, SIGTERM, 0, dispatch_get_main_queue());
    dispatch_source_set_event_handler(self.terminationSignal, ^{ [NSApp terminate:nil]; });
    dispatch_resume(self.terminationSignal);
    NSMutableArray *args = [NSMutableArray array];
    NSString *port = @"18765";
    NSArray *incoming = NSProcessInfo.processInfo.arguments;
    for (NSUInteger i = 1; i < incoming.count; i++) {
        NSString *arg = incoming[i];
        if ([arg isEqualToString:@"--no-open"]) { self.noOpen = YES; continue; }
        if (([arg isEqualToString:@"--port"] || [arg isEqualToString:@"--data-dir"]) && i + 1 < incoming.count) {
            NSString *value = incoming[++i]; [args addObjectsFromArray:@[arg, value]];
            if ([arg isEqualToString:@"--port"]) port = value;
        }
    }
    self.serverArguments = args;
    self.workbenchURL = [NSURL URLWithString:[NSString stringWithFormat:@"http://127.0.0.1:%@/#search", port]];
    NSString *logs = [NSHomeDirectory() stringByAppendingPathComponent:@"Library/Logs/Yingsha Lead Finder"];
    [[NSFileManager defaultManager] createDirectoryAtPath:logs withIntermediateDirectories:YES attributes:@{NSFilePosixPermissions:@0700} error:nil];
    self.logURL = [NSURL fileURLWithPath:[logs stringByAppendingPathComponent:@"desktop.log"]];
    self.statusItem = [[NSStatusBar systemStatusBar] statusItemWithLength:NSVariableStatusItemLength];
    self.statusItem.button.title = @"颖纱"; self.statusItem.button.toolTip = @"客户线索工具";
    NSMenu *menu = [[NSMenu alloc] init];
    for (NSArray *item in @[@[@"打开工作台", NSStringFromSelector(@selector(openWorkbench:))], @[@"查看运行日志", NSStringFromSelector(@selector(openLog:))], @[@"退出客户线索工具", NSStringFromSelector(@selector(quit:))]]) {
        NSMenuItem *entry = [[NSMenuItem alloc] initWithTitle:item[0] action:NSSelectorFromString(item[1]) keyEquivalent:@""];
        entry.target = self; [menu addItem:entry];
    }
    self.statusItem.menu = menu; [self checkService];
}
- (void)startService {
    NSString *resources = NSBundle.mainBundle.resourcePath;
#if defined(__arm64__)
    NSString *arch = @"arm64";
#else
    NSString *arch = @"x86_64";
#endif
    NSString *python = [resources stringByAppendingPathComponent:[NSString stringWithFormat:@"runtime/%@/bin/python3.13", arch]];
    self.service = [[NSTask alloc] init];
    self.service.executableURL = [NSURL fileURLWithPath:python];
    self.service.currentDirectoryURL = [NSURL fileURLWithPath:resources];
    NSMutableArray *args = [NSMutableArray arrayWithArray:@[@"-I", @"-B", [resources stringByAppendingPathComponent:@"bootstrap.py"]]];
    [args addObjectsFromArray:self.serverArguments]; self.service.arguments = args;
    if (![[NSFileManager defaultManager] fileExistsAtPath:self.logURL.path])
        [[NSFileManager defaultManager] createFileAtPath:self.logURL.path contents:nil attributes:@{NSFilePosixPermissions:@0600}];
    NSFileHandle *log = [NSFileHandle fileHandleForWritingAtPath:self.logURL.path];
    [log seekToEndOfFile]; self.service.standardOutput = log; self.service.standardError = log;
    NSError *error = nil;
    if (![self.service launchAndReturnError:&error]) {
        [self showError:@"内置运行环境未能启动，请将应用完整复制到“应用程序”文件夹，再重新打开。"]; return;
    }
    __weak LeadAppDelegate *weakSelf = self;
    self.service.terminationHandler = ^(NSTask *task) {
        dispatch_async(dispatch_get_main_queue(), ^{
            LeadAppDelegate *strongSelf = weakSelf;
            if (strongSelf && !strongSelf.stopping && task.terminationStatus != 0)
                [strongSelf showError:@"服务启动失败或端口已被占用。可查看日志了解原因，再重新打开应用。"];
        });
    };
    self.timer = [NSTimer scheduledTimerWithTimeInterval:0.4 target:self selector:@selector(checkService) userInfo:nil repeats:YES];
}
- (void)checkService {
    if (self.checking) return;
    self.checking = YES;
    NSString *address = [[self.workbenchURL.absoluteString componentsSeparatedByString:@"/#"] firstObject];
    NSURL *url = [NSURL URLWithString:[address stringByAppendingString:@"/api/status"]];
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:url cachePolicy:NSURLRequestReloadIgnoringLocalCacheData timeoutInterval:1];
    [[[NSURLSession sharedSession] dataTaskWithRequest:request completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        NSDictionary *status = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
        BOOL matched = [status isKindOfClass:[NSDictionary class]] && [status[@"app"] isEqual:@"yingsha-lead-finder"];
        dispatch_async(dispatch_get_main_queue(), ^{
            self.checking = NO;
            if (self.stopping) return;
            if (matched) {
                self.ready = YES; [self.timer invalidate];
                if (!self.noOpen) [self openWorkbench:nil];
            } else if (!self.service && error) {
                [self startService];
            } else if (!self.service) {
                [self showError:@"工作台的本地端口已被其他程序占用。请关闭占用程序后重试。"];
            } else if (++self.attempts >= 60) {
                [self showError:@"服务未能及时启动。请查看运行日志后重新打开应用。"];
            }
        });
    }] resume];
}
- (void)openWorkbench:(id)sender {
    if (self.ready) [[NSWorkspace sharedWorkspace] openURL:self.workbenchURL];
}
- (void)openLog:(id)sender { [[NSWorkspace sharedWorkspace] openURL:self.logURL]; }
- (void)quit:(id)sender { [NSApp terminate:nil]; }
- (BOOL)applicationShouldHandleReopen:(NSApplication *)app hasVisibleWindows:(BOOL)visible {
    [self openWorkbench:nil]; return YES;
}
- (void)applicationWillTerminate:(NSNotification *)notification {
    self.stopping = YES; [self.timer invalidate];
    if (self.service.running) { [self.service terminate]; [self.service waitUntilExit]; }
}
@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSApplication *app = NSApplication.sharedApplication;
        LeadAppDelegate *delegate = [[LeadAppDelegate alloc] init];
        app.delegate = delegate;
        [app setActivationPolicy:NSApplicationActivationPolicyAccessory];
        [app run];
    }
    return 0;
}
