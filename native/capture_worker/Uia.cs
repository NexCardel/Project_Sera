// native/capture_worker/Uia.cs - the UI Automation COM interfaces the capture worker calls.
//
// Hand-declared (no tlbimp on the build PCs): only the vtable ORDER must match UIAutomationClient.h.
// Slots this worker never calls are declared as _gN() placeholders, so the slots after them line up.
// The order was taken from comtypes' generated UIAutomationClient wrapper (the typelib itself).
// Strictly passive: nothing here that the worker calls clicks, focuses, types or sets a value.

using System;
using System.Runtime.InteropServices;

namespace SeraCapture
{
    [ComImport, Guid("352FFBA8-0973-437C-A61F-F64CAFD81DF9"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IUIAutomationCondition { }

    [ComImport, Guid("14314595-B4BC-4055-95F2-58F2E42C9855"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IUIAutomationElementArray
    {
        int Length { get; }
        IUIAutomationElement GetElement(int index);
    }

    [ComImport, Guid("B32A92B5-BC25-4078-9C08-D7EE95C48E03"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IUIAutomationCacheRequest
    {
        void AddProperty(int propertyId);
        void _g1();                                   // AddPattern
        void _g2();                                   // Clone
        void _g3();                                   // get_TreeScope
        void SetTreeScope(int scope);                 // put_TreeScope
        void _g5();                                   // get_TreeFilter
        void SetTreeFilter(IUIAutomationCondition filter);
        void _g7();                                   // get_AutomationElementMode
        void SetAutomationElementMode(int mode);
    }

    [ComImport, Guid("D22108AA-8AC5-49A5-837B-37BBB3D7591E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IUIAutomationElement
    {
        void _g0();                                   // SetFocus - never called
        [return: MarshalAs(UnmanagedType.SafeArray, SafeArraySubType = VarEnum.VT_I4)]
        int[] GetRuntimeId();
        void _g2();                                   // FindFirst
        IUIAutomationElementArray FindAll(int scope, IUIAutomationCondition condition);
        void _g4();                                   // FindFirstBuildCache
        IUIAutomationElementArray FindAllBuildCache(int scope, IUIAutomationCondition condition, IUIAutomationCacheRequest cacheRequest);
        IUIAutomationElement BuildUpdatedCache(IUIAutomationCacheRequest cacheRequest);
        [return: MarshalAs(UnmanagedType.Struct)]
        object GetCurrentPropertyValue(int propertyId);
        void _g8();                                   // GetCurrentPropertyValueEx
        [return: MarshalAs(UnmanagedType.Struct)]
        object GetCachedPropertyValue(int propertyId);
        void _g10();                                  // GetCachedPropertyValueEx
        void _g11();                                  // GetCurrentPatternAs
        void _g12();                                  // GetCachedPatternAs
        [return: MarshalAs(UnmanagedType.IUnknown)]
        object GetCurrentPattern(int patternId);
        void _g14();                                  // GetCachedPattern
        void _g15();                                  // GetCachedParent
        IUIAutomationElementArray GetCachedChildren();
        void _g17();                                  // CurrentProcessId
        void _g18();                                  // CurrentControlType
        void _g19();                                  // CurrentLocalizedControlType
        string CurrentName { [return: MarshalAs(UnmanagedType.BStr)] get; }
        void _g21(); void _g22(); void _g23(); void _g24(); void _g25();
        string CurrentAutomationId { [return: MarshalAs(UnmanagedType.BStr)] get; }
        void _g27(); void _g28(); void _g29(); void _g30(); void _g31(); void _g32(); void _g33(); void _g34();
        int CurrentIsOffscreen { get; }
    }

    [ComImport, Guid("A94CD8B1-0844-4CD6-9D2D-640537AB39E9"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IUIAutomationValuePattern
    {
        void _g0();                                   // SetValue - never called
        string CurrentValue { [return: MarshalAs(UnmanagedType.BStr)] get; }
    }

    [ComImport, Guid("A8EFA66A-0FDA-421A-9194-38021F3578EA"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IUIAutomationSelectionItemPattern
    {
        void _g0(); void _g1(); void _g2();           // Select, AddToSelection, RemoveFromSelection - never called
        int CurrentIsSelected { get; }
    }

    [ComImport, Guid("146C3C17-F12E-4E22-8C27-F894B9B79C69"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IUIAutomationEventHandler
    {
        void HandleAutomationEvent(IUIAutomationElement sender, int eventId);
    }

    [ComImport, Guid("C7CB2637-E6C2-4D0C-85DE-4948C02175C7"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IUIAutomationNotificationEventHandler
    {
        void HandleNotificationEvent(IUIAutomationElement sender, int notificationKind, int notificationProcessing,
                                     [MarshalAs(UnmanagedType.BStr)] string displayString,
                                     [MarshalAs(UnmanagedType.BStr)] string activityId);
    }

    // IUIAutomation, slots 0-54.
    [ComImport, Guid("30CBE57D-D9D0-452A-AB13-7AC5AC4825EE"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IUIAutomation
    {
        void _g0(); void _g1(); void _g2();           // CompareElements, CompareRuntimeIds, GetRootElement
        IUIAutomationElement ElementFromHandle(IntPtr hwnd);
        void _g4(); void _g5(); void _g6(); void _g7(); void _g8(); void _g9();
        void _g10(); void _g11(); void _g12(); void _g13();
        IUIAutomationCondition RawViewCondition();    // get_RawViewCondition
        void _g15(); void _g16();
        IUIAutomationCacheRequest CreateCacheRequest();
        void _g18(); void _g19();
        IUIAutomationCondition CreatePropertyCondition(int propertyId, [MarshalAs(UnmanagedType.Struct)] object value);
        void _g21(); void _g22(); void _g23(); void _g24();
        IUIAutomationCondition CreateOrCondition(IUIAutomationCondition c1, IUIAutomationCondition c2);
        void _g26(); void _g27(); void _g28();
        void AddAutomationEventHandler(int eventId, IUIAutomationElement element, int scope,
                                       IUIAutomationCacheRequest cacheRequest, IUIAutomationEventHandler handler);
        void RemoveAutomationEventHandler(int eventId, IUIAutomationElement element, IUIAutomationEventHandler handler);
    }

    // IUIAutomation5 flat: IUIAutomation (0-54), 2 (55-60), 3 (61-62), 4 (63-64), 5 (65-66).
    [ComImport, Guid("25F700C8-D816-4057-A9DC-3CBDEE77E256"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IUIAutomation5
    {
        void _g0(); void _g1(); void _g2(); void _g3(); void _g4(); void _g5(); void _g6(); void _g7(); void _g8(); void _g9();
        void _g10(); void _g11(); void _g12(); void _g13(); void _g14(); void _g15(); void _g16(); void _g17(); void _g18(); void _g19();
        void _g20(); void _g21(); void _g22(); void _g23(); void _g24(); void _g25(); void _g26(); void _g27(); void _g28(); void _g29();
        void _g30(); void _g31(); void _g32(); void _g33(); void _g34(); void _g35(); void _g36(); void _g37(); void _g38(); void _g39();
        void _g40(); void _g41(); void _g42(); void _g43(); void _g44(); void _g45(); void _g46(); void _g47(); void _g48(); void _g49();
        void _g50(); void _g51(); void _g52(); void _g53(); void _g54();
        void _g55(); void _g56(); void _g57(); void _g58(); void _g59(); void _g60();
        void _g61(); void _g62(); void _g63(); void _g64();
        void AddNotificationEventHandler(IUIAutomationElement element, int scope, IUIAutomationCacheRequest cacheRequest,
                                         IUIAutomationNotificationEventHandler handler);
        void RemoveNotificationEventHandler(IUIAutomationElement element, IUIAutomationNotificationEventHandler handler);
    }

    public static class Uia
    {
        public static readonly Guid CLSID_CUIAutomation = new Guid("FF48DBA4-60EF-4201-AA87-54103EEF594E");
        public static readonly Guid CLSID_CUIAutomation8 = new Guid("E22AD333-B25F-460C-83D0-0581107395C9");

        // TreeScope
        public const int SCOPE_ELEMENT = 1, SCOPE_CHILDREN = 2, SCOPE_DESCENDANTS = 4, SCOPE_SUBTREE = 7;
        // Patterns
        public const int PATTERN_VALUE = 10002, PATTERN_SELECTION_ITEM = 10010;
        // Properties (UIAutomationClient.h)
        public const int PID_BOUNDING_RECT = 30001, PID_CONTROL_TYPE = 30003, PID_NAME = 30005, PID_AUTOMATION_ID = 30011,
            PID_IS_PASSWORD = 30019, PID_IS_OFFSCREEN = 30022, PID_FRAMEWORK_ID = 30024, PID_VALUE = 30045,
            PID_GRID_ROW = 30064, PID_GRID_COLUMN = 30065, PID_SELECTION_IS_SELECTED = 30079, PID_TOGGLE_STATE = 30086,
            PID_ARIA_ROLE = 30101, PID_LANDMARK_TYPE = 30157, PID_HEADING_LEVEL = 30173;
        // Control types
        public const int CT_CHECKBOX = 50002, CT_COMBOBOX = 50003, CT_EDIT = 50004, CT_RADIOBUTTON = 50013,
            CT_SPINNER = 50016, CT_TABITEM = 50019, CT_CUSTOM = 50025, CT_GROUP = 50026, CT_DATAITEM = 50029,
            CT_DOCUMENT = 50030, CT_PANE = 50033;

        public static object Create(Guid clsid)
        {
            return Activator.CreateInstance(Type.GetTypeFromCLSID(clsid, true));
        }

        public static IUIAutomation New()
        {
            return (IUIAutomation)Create(CLSID_CUIAutomation);
        }

        [DllImport("user32.dll")]
        public static extern bool IsWindow(IntPtr hwnd);

        [DllImport("user32.dll")]
        public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    }
}
