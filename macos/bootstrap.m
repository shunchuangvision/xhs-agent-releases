#import <Cocoa/Cocoa.h>
#include <unistd.h>

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSString *resources = [[NSBundle mainBundle] resourcePath];
        NSString *runtime = [resources stringByAppendingPathComponent:@"payload/launcher/deps/3.2.0/xhs-runtime"];
        // Resolve the bundled version, independent of the installed program version.
        NSData *data = [NSData dataWithContentsOfFile:[resources stringByAppendingPathComponent:@"payload/version.json"]];
        NSDictionary *version = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
        if (version[@"dependencies"]) {
            runtime = [resources stringByAppendingPathComponent:[NSString stringWithFormat:@"payload/launcher/deps/%@/xhs-runtime", version[@"dependencies"]]];
        }
        NSString *payload = [resources stringByAppendingPathComponent:@"payload"];
        // exec inherits Finder's application launch context and opens no terminal.
        const char *args[] = {[runtime fileSystemRepresentation], "--bootstrap", [payload fileSystemRepresentation], argc > 1 ? argv[1] : NULL, NULL};
        execv(args[0], (char *const *)args);
        [NSApplication sharedApplication];
        NSAlert *alert = [[NSAlert alloc] init];
        alert.messageText = @"XHS Agent 启动未完成";
        alert.informativeText = @"应用文件不完整。请重新下载并完整解压 Mac 安装包。";
        [alert runModal];
        return 1;
    }
}
